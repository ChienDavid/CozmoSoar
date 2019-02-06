import PySoarLib as psl
import soar.Python_sml_ClientInterface as sml

import cozmo
from cozmo.util import radians, degrees, distance_mm, speed_mmps

from c_soar_util import *


class CozmoSoar(psl.AgentConnector):
    """
    A class representing the Soar interface with a Cozmo robot.

    The `CozmoSoar`class is a concrete instantiation of the `AgentConnector` class from Aaron
    Mininger's PySoarLib, which provides a way to connect with a running Soar kernel in Python
    with callbacks. The purpose of the `CozmoSoar` class is to provide a custom way to connect
    the Cozmo robot with Soar by updating the appropriate input link attributes and interpreting
    the resulting output link commands.
    """

    def __init__(self, agent: psl.SoarAgent, robot: cozmo.robot):
        """
        Create an instance of the `CozmoSoar` class connecting the agent to the robot.

        :param agent: The `SoarAgent` object which represents the agent which should control this
                      Cozmo.
        :param robot: The Cozmo `robot` instance representing the Cozmo robot being controlled.
        """
        super(CozmoSoar, self).__init__(agent)
        self.name = self.agent.agent_name
        self.robot = self.r = robot
        self.world = self.w = self.r.world

        self.cam = self.r.camera
        self.cam.image_stream_enabled = True
        self.r.enable_facial_expression_estimation()

        self.objects = {}
        self.faces = {}
        self.running_actions = []

        #######################
        # Working Memory data #
        #######################

        # self.static_inputs maps each static input to a function to retrieve its latest value from
        #   Soar. A static input is one that won't ever disappear, in contrast to temporary inputs
        #   like faces or objects
        self.static_inputs = {'battery_voltage': lambda: self.r.battery_voltage,
                              'carrying_block': lambda: int(self.r.is_carrying_block),
                              'carrying_object_id': lambda: self.r.carrying_object_id,
                              'charging': lambda: int(self.r.is_charging),
                              'cliff_detected': lambda: int(self.r.is_cliff_detected),
                              'head_angle': lambda: self.r.head_angle.radians,
                              'face_count': self.w.visible_face_count,
                              'object_count': lambda : len(self.objects),
                              'picked_up': lambda: int(self.r.is_picked_up),
                              'robot_id': lambda: self.r.robot_id,
                              'serial': lambda: self.r.serial,
                              'pose': {'rot': lambda: self.r.pose.rotation.angle_z.radians,
                                       'x': lambda: self.r.pose.position.x,
                                       'y': lambda: self.r.pose.position.y,
                                       'z': lambda: self.r.pose.position.z},
                              'lift': {'angle': lambda: self.r.lift_angle.radians,
                                       'height': lambda: self.r.lift_height.distance_mm,
                                       'ratio': lambda: self.r.lift_ratio}
                              }

        # self.WMEs maps SoarWME objects to their attribute names for easier retrieval. Since Cozmo
        #   inputs will always be one-to-one with their values (i.e., there won't be multiple values
        #   with the same name), a standard dictionary is fine
        self.WMEs = {}

        ###############################
        # Command Handling dictionary #
        ###############################
        self.command_map = {"move-lift": self.__handle_move_lift,
                            "go-to-object": self.__handle_go_to_object,
                            "move-head": self.__handle_move_head,
                            "turn-to-face": self.__handle_turn_to_face,
                            "set-backpack-lights": self.__handle_set_backpack_lights,
                            "drive-forward": self.__handle_drive_forward,
                            "turn-in-place": self.__handle_turn_in_place,
                            "pick-up-object": self.__handle_pick_up_object,
                            "place-object-down": self.__handle_place_object_down,
                            "place-on-object": self.__handle_place_on_object,
                            "dock-with-cube": self.__handle_dock_with_cube}

    def on_output_event(self, command_name: str, root_id: sml.Identifier):
        """
        Handle commands Soar outputs by initiating the appropriate Soar action.

        Currently, all this does is use a dictionary mapping from the command name to the
        appropriate handling function.

        :param command_name: Name of the command being issued
        :param root_id: sml Identifier object containing the command
        :return: None
        """
        self.command_map[command_name](root_id)
        root_id.AddStatusComplete()

    def __handle_place_object_down(self, command):
        """
        Handle a Soar place-object-down action.

        The Sour output should look like:
        (I3 ^place-object-down)
        Cozmo will lower the lift until the object is placed on the ground, then back up.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        print("Placing object down")
        place_object_down_action = self.r.place_object_on_ground_here(0)
        self.running_actions.append((command, place_object_down_action))
        # callback = self.__handle_action_complete_factory(command)
        # place_object_down_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def __handle_place_on_object(self, command):
        """
        Handle a Soar place-on-object action.

        The Sour output should look like:
        (I3 ^place-on-object Vx)
          (Vx ^target_object_id [id])
        where [id] is the object id of the object that Cozmo should place to object its holding
        on top of.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            target_id = int(command.GetParameterValue("target_object_id"))
        except ValueError as e:
            print("Invalid target-object-id format {}".format(
                command.GetParameterValue("target_object_id")))
            return False
        if target_id not in self.objects.keys():
            print("Couldn't find target object")
            return False

        print("Placing held object on top of {}".format(target_id))
        target_obj = self.objects[target_id]
        place_on_object_action = self.robot.place_on_object(target_obj)
        self.running_actions.append((command, place_on_object_action))
        # callback = self.__handle_action_complete_factory(command)
        # place_on_object_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def __handle_dock_with_cube(self, command):
        """
        Handle a Soar dock-with-cube action.

        The Sour output should look like:
        (I3 ^dock-with-cube Vx)
          (Vx ^object_id [id])
        where [id] is the object id of the cube to dock with. Cozmo will approach the cube until
        its lift hooks are under the grip holes.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            target_id = int(command.GetParameterValue("object_id"))
        except ValueError as e:
            print("Invalid target-object-id format {}".format(
                command.GetParameterValue("object_id")))
            return False
        if target_id not in self.objects.keys():
            print("Couldn't find target object")
            return False

        print("Docking with cube with object id {}".format(target_id))
        target_obj = self.objects[target_id]
        dock_with_cube_action = self.robot.dock_with_cube(target_obj)
        self.running_actions.append((command, dock_with_cube_action))
        # callback = self.__handle_action_complete_factory(command)
        # dock_with_cube_action.wait_for_completed(timeout=10)
        # command.AddStatusComplete()
        return True

    def __handle_pick_up_object(self, command):
        """
        Handle a Soar pick-up-object action.

        The Sour output should look like:
        (I3 ^pick-up-object Vx)
          (Vx ^object_id [id])
        where [id] is the object id of the object to pick up. Cozmo will approach the object
        autonomously and try to grasp it with its lift, then lift the lift up. This action is
        partiularly prone to failing.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            target_id = int(command.GetParameterValue("object_id"))
        except ValueError as e:
            print("Invalid object-id format {}".format(command.GetParameterValue("object_id")))
            return False

        obj_designation = "obj{}".format(target_id)
        if not self.objects.get(obj_designation):
            print("Couldn't find target object")
            return False

        print("Picking up object {}".format(obj_designation))
        target_obj = self.objects[obj_designation]
        pick_up_object_action = self.robot.pickup_object(target_obj)
        pick_up_object_action.wait_for_completed()
        # self.running_actions.append((command, pick_up_object_action))
        # callback = self.__handle_action_complete_factory(command)
        # pick_up_object_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def __handle_turn_to_face(self, command):
        """
        Handle a Soar turn-to-face action.

        The Soar output should look like:
        (I3 ^turn-to-face Vx)
          (Vx ^face-id [fid])
        where [fid] is the integer ID associated with the face to turn towards.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            fid = int(command.GetParameterValue("face-id"))
        except ValueError as e:
            print("Invalid face id format {}".format(command.GetParameterValue("face-id")))
            return False
        if fid not in self.faces.keys():
            print("Face {} not recognized".format(fid))
            return False

        print("Turning to face {}".format(fid))
        target_face = self.faces[fid]
        turn_towards_face_action = self.r.turn_towards_face(target_face)
        self.running_actions.append((command, turn_towards_face_action))
        # callback = self.__handle_action_complete_factory(command)
        # turn_towards_face_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def __handle_move_lift(self, command):
        """
        Handle a Soar move-lift action.

        The Soar output should look like:
        (I3 ^move-lift Vx)
          (Vx ^height [hgt])
        where [hgt] is a real number in the range [0, 1]. This command moves the lift to the
        the given height, where 0 is the lowest possible position and 1 is the highest.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            height = float(command.GetParameterValue("height"))
        except ValueError as e:
            print("Invalid height format {}".format(command.GetParameterValue("height")))
            return False

        print("Moving lift {}".format(height))
        set_lift_height_action = self.robot.set_lift_height(height)
        self.running_actions.append((command, set_lift_height_action))
        # callback = self.__handle_action_complete_factory(command)
        # set_lift_height_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def __handle_move_head(self, command):
        """
        Handle a Soar move-head action.

        The Soar output should look like:
        (I3 ^move-head Vx)
          (Vx ^angle [ang])
        where [ang] is a real number in the range [-0.44, 0.78]. This command moves the head to the
        the given angle, where 0 is looking straight ahead and the angle is radians from that
        position.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            angle = float(command.GetParameterValue("angle"))
        except ValueError as e:
            print("Invalid angle format {}".format(command.GetParameterValue("angle")))
            return False

        print("Moving head {}".format(angle))
        set_head_angle_action = self.robot.set_head_angle(radians(angle))
        set_head_angle_action.wait_for_completed()
        print("Done moving head")
        # self.running_actions.append((command, set_head_angle_action))
        # callback = self.__handle_action_complete_factory(command)
        # set_head_angle_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def __handle_go_to_object(self, command):
        """
        Handle a Soar go-to-object action.

        The Sour output should look like:
        (I3 ^go-to-object Vx)
          (Vx ^target_object_id [id])
        where [id] is the object id of the object to go to. Cozmo will stop 150mm from the object.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            target_id = int(command.GetParameterValue("target_object_id"))
        except ValueError as e:
            print("Invalid target-object-id format {}".format(command.GetParameterValue("target_object_id")))
            return False
        if target_id not in self.objects.keys():
            print("Couldn't find target object")
            return False

        print("Going to object {}".format(target_id))
        target_obj = self.objects[target_id]
        go_to_object_action = self.robot.go_to_object(target_obj, distance_mm(100))
        self.running_actions.append((command, go_to_object_action))
        # callback = self.__handle_action_complete_factory(command)
        # go_to_object_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def __handle_set_backpack_lights(self, command):
        """
        Handle a Soar set-backpack-lights action.

        The Sour output should look like:
        (I3 ^set-backpack-lights Vx)
          (Vx ^color [color])
        where [color] is a string indicating which color the lights should be set to. The colors
        are "red", "blue", "green", "white", and "off".

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        color_str = command.GetParameterValue("color")
        if color_str not in COLORS:
            print("Invalid backpack lights color {}".format(color_str))
            return False
        elif color_str == 'red':
            light = cozmo.lights.red_light
        elif color_str == 'green':
            light = cozmo.lights.green_light
        elif color_str == 'blue':
            light = cozmo.lights.blue_light
        elif color_str == 'white':
            light = cozmo.lights.white_light
        else:
            light = cozmo.lights.off_light

        self.r.set_all_backpack_lights(light=light)
        command.AddStatusComplete()
        return True

    def __handle_drive_forward(self, command):
        """
        Handle a Soar drive-forward action.

        The Sour output should look like:
        (I3 ^drive-forward Vx)
          (Vx ^distance [dist]
              ^speed [spd])
        where [dist] is a real number indicating how far Cozmo should travel (negatives go
        backwards) and speed is how fast Cozmo should travel. Units are mm and mm/s, respectively.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            distance = distance_mm(float(command.GetParameterValue("distance")))
        except ValueError as e:
            print("Invalid distance format {}".format(command.GetParameterValue("distance")))
            return False
        try:
            speed = speed_mmps(float(command.GetParameterValue("speed")))
        except ValueError as e:
            print("Invalid speed format {}".format(command.GetParameterValue("speed")))
            return False

        print("Driving forward {}mm at {}mm/s".format(distance.distance_mm, speed.speed_mmps))
        drive_forward_action = self.r.drive_straight(distance, speed)
        self.running_actions.append((command, drive_forward_action))
        # callback = self.__handle_action_complete_factory(command)
        # drive_forward_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def __handle_turn_in_place(self, command):
        """
        Handle a Soar turn-in-place action.

        The Sour output should look like:
        (I3 ^turn-in-place Vx)
          (Vx ^angle [ang]
              ^speed [spd])
        where [ang] is the amount Cozmo should rotate in degrees and speed is the speed at which
        Cozmo should rotate in deg/s.

        :param command: Soar command object
        :return: True if successful, False otherwise
        """
        try:
            angle = degrees(float(command.GetParameterValue("angle")))
        except ValueError as e:
            print("Invalid angle format {}".format(command.GetParameterValue("angle")))
            return False
        try:
            speed = degrees(float(command.GetParameterValue("speed")))
        except ValueError as e:
            print("Invalid speed format {}".format(command.GetParameterValue("speed")))
            return False

        print("Rotating in place {} degrees at {}deg/s".format(angle.degrees, speed.degrees))
        turn_in_place_action = self.r.turn_in_place(angle=angle, speed=speed)
        self.running_actions.append((command, turn_in_place_action))
        # callback = self.__handle_action_complete_factory(command)
        # turn_in_place_action.add_event_handler(EvtActionCompleted, callback)
        return True

    def on_input_phase(self, input_link: sml.Identifier):
        """
        Prior to each input phase, update the changed values of Soar's input link

        Scan through the designated Cozmo inputs and update the corresponding WMEs in Soar via
        instances of the `SoarWME` class. For each input, we first get the value, then check
        whether there exists a WME with that attribute name. If not, we add one to the Soar agent
        and the WME dict of the `CozmoSoar` object. Otherwise, we retrieve the `SoarWME` object
        associated with the input and update its value, then call its `update_wm` method. For
        terminal WMEs, this is simple. However, for sub-trees we need to recursively update
        the WMEs.

        We have to handle temporary inputs e.g., faces or objects, differently, because they
        need to be removed when they are no longer detected.

        :param input_link: The Soar WME corresponding to the input link of the agent.
        :return: None
        """
        # First, we handle inputs which will always be present
        for input_name in self.static_inputs.keys():
            new_val = self.static_inputs[input_name]
            wme = self.WMEs.get(input_name)

            if not callable(new_val):
                if wme is None:
                    wme = input_link.CreateIdWME(input_name)
                    self.WMEs[input_name] = wme
                self.__input_recurse(new_val, input_name, wme)
                continue

            new_val = new_val()
            if wme is None:
                new_wme = psl.SoarWME(att=input_name,
                                      val=new_val)
                self.WMEs[input_name] = new_wme
                new_wme.add_to_wm(input_link)
            else:
                wme.set_value(new_val)
                wme.update_wm()

        # Then, check through the visible faces and objects to see if they need to be added,
        # updated, or removed
        vis_faces = set(list(self.w.visible_faces))
        for face in vis_faces:
            face_designation = "face{}".format(face.face_id)
            if face_designation in self.faces:
                face_wme = self.WMEs[face_designation]
            else:
                self.faces[face_designation] = face
                face_wme = input_link.CreateIdWME("face")
                self.WMEs[face_designation] = face_wme
            self.__build_face_wme_subtree(face, face_designation, face_wme)

        faces_missing = set()
        for face_dsg in self.faces.keys():
            if self.faces[face_dsg] not in vis_faces:
                faces_missing.add(face_dsg)
        for face_dsg in faces_missing:
            del self.faces[face_dsg]
            self.WMEs[face_dsg].DestroyWME()
            del self.WMEs[face_dsg]

        vis_objs = set(list(self.w.visible_objects))
        for obj in vis_objs:
            obj_designation = "obj{}".format(obj.object_id)
            if obj_designation in self.objects:
                obj_wme = self.WMEs[obj_designation]
            else:
                print("!!!B")
                self.objects[obj_designation] = obj
                obj_wme = input_link.CreateIdWME("object")
                self.WMEs[obj_designation] = obj_wme
            print("!!!C")
            self.__build_obj_wme_subtree(obj, obj_designation, obj_wme)

        objs_missing = set()
        for obj_dsg in self.objects.keys():
            if self.objects[obj_dsg] not in vis_objs:
                objs_missing.add(obj_dsg)
        for obj_dsg in objs_missing:
            del self.objects[obj_dsg]
            self.__destroy_obj_wme_subtree(obj_dsg)

    def __destroy_obj_wme_subtree(self, wme_name):
        """
        Recursively destroy all WMEs rooted at the object with the given wme_name.

        :param obj_dsg:
        :return: None
        """
        for sub_tree_root_name in self.WMEs.keys():
            if sub_tree_root_name.startswith(wme_name) and sub_tree_root_name != wme_name:
                self.__destroy_obj_wme_subtree(sub_tree_root_name)
        wme = self.WMEs[wme_name]
        if isinstance(wme, psl.SoarWME):
            wme.remove_from_wm()
        elif isinstance(wme, sml.Identifier):
            wme.DestroyWME()
        else:
            raise Exception("WME was not one of the two correct types")
        del self.WMEs[wme_name]

    def __build_obj_wme_subtree(self, obj, obj_designation, obj_wme):
        """
        Build a working memory sub-tree for a given perceived object

        :param obj: Cozmo objects.ObservableObject object to put into working memory
        :param obj_designation: Unique string name of the object
        :param obj_wme: sml identifier at the root of the object sub-tree
        :return: None
        """
        obj_input_dict = {'object_id': obj.object_id,
                         'descriptive_name': obj.descriptive_name,
                         'distance': obj_distance_factory(self.r, obj)(),
                         'heading': obj_heading_factory(self.r, obj)(),
                         'liftable': int(obj.pickupable),
                         'type': "object"}
        if isinstance(obj, cozmo.objects.LightCube):
            obj_input_dict['type'] = "cube"
            obj_input_dict['connected'] = obj.is_connected
            obj_input_dict['cube_id'] = obj.cube_id
            obj_input_dict['moving'] = obj.is_moving

        print(obj_input_dict)
        for input_name in obj_input_dict.keys():
            wme = self.WMEs.get(obj_designation + '.' + input_name)
            print(obj_designation + '.' + input_name)
            if wme is None:
                wme = psl.SoarWME(input_name, obj_input_dict[input_name])
                wme.add_to_wm(obj_wme)
                self.WMEs[obj_designation + '.' + input_name] = wme
            else:
                wme.set_value(obj_input_dict[input_name])
                wme.update_wm()

    def __build_face_wme_subtree(self, face, face_designation, face_wme):
        """
        Build a working memory sub-tree for a given perceived face

        :param face: Cozmo faces.Face object to put into working memory
        :param face_designation: Unique string name of the face
        :param face_wme: sml identifier at the root of the face sub-tree
        :return: None
        """
        face_input_dict = {'expression': face.expression,
                           'expression_score': face.expression_score,
                           'face_id': face.face_id,
                           'name': face.name if face.name != '' else 'unknown',
                           'distance': obj_distance_factory(self.r, face)(),
                           'heading': obj_heading_factory(self.r, face)()
                           }
        for input_name in face_input_dict.keys():
            wme = self.WMEs.get(face_designation+'.'+input_name)
            if wme is None:
                wme = psl.SoarWME(input_name, face_input_dict[input_name])
                wme.add_to_wm(face_wme)
                self.WMEs[face_designation+'.'+input_name] = wme
            else:
                wme.set_value(face_input_dict[input_name])
                wme.update_wm()

    def __input_recurse(self, input_dict, root_name, root_id: sml.Identifier):
        """
        Recursively update WMEs that have a sub-tree structure in the input link.

        We scan through the `input_dict`, which represents the input value getters (or further
        sub-trees) of the sub-tree root, either adding terminal WMEs as usual or further recursing.

        :param input_dict: A dict mapping attributes to getter functions
        :param root_name: The attribute which is the root of this sub-tree
        :param root_id: The sml identifier of the root of the sub-tree
        :return: None
        """
        assert isinstance(input_dict, dict), "Should only recurse on dicts!"

        for input_name in input_dict.keys():
            new_val = input_dict[input_name]
            wme = self.WMEs.get(root_name+'.'+input_name)

            if not callable(new_val):
                if wme is None:
                    wme = root_id.CreateIdWME(input_name)
                    self.WMEs[root_name+'.'+input_name] = wme
                self.__input_recurse(new_val, root_name+'.'+input_name, wme)
                continue

            new_val = new_val()
            if wme is None:
                new_wme = psl.SoarWME(att=input_name,
                                      val=new_val)
                self.WMEs[root_name+'.'+input_name] = new_wme
                new_wme.add_to_wm(root_id)
            else:
                wme.set_value(new_val)
                wme.update_wm()


class SoarObserver(psl.AgentConnector):
    """
    An `AgentConnector` subclass for viewing infromation about the Soar agent.

    This class just exists to handle getting information out of Soar and into a useful format.
    """
    def __init__(self, agent: psl.SoarAgent, print_handler=None):
        super(SoarObserver, self).__init__(agent, print_handler)

    def on_input_phase(self, input_link):
        print("State:")
        self.agent.execute_command("print --depth 2 s1")
        print("Input link:")
        self.agent.execute_command("print --depth 3 i2")
        print("Output link:")
        self.agent.execute_command("print --depth 4 i3")
