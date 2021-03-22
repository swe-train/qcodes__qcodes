"""
This file holds the QCoDeS driver for the Galil DMC-41x3 motor controllers,
colloquially known as the "stepper motors".
"""
from typing import Any, Dict, Optional, List, Tuple
import numpy as np

from qcodes.instrument.base import Instrument
from qcodes.instrument.channel import InstrumentChannel
from qcodes.instrument.group_parameter import GroupParameter, Group
from qcodes.utils.validators import Enum, Ints, Union

try:
    import gclib
except ImportError as e:
    raise ImportError(
        "Cannot find gclib library. Download gclib installer from "
        "https://www.galil.com/sw/pub/all/rn/gclib.html and install Galil "
        "motion controller software for your OS. Afterwards go "
        "to https://www.galil.com/sw/pub/all/doc/gclib/html/python.html and "
        "follow instruction to be able to import gclib package in your "
        "environment.") from e


class GalilMotionController(Instrument):
    """
    Base class for Galil Motion Controller drivers
    """
    def __init__(self, name: str, address: str, **kwargs: Any) -> None:
        super().__init__(name=name, **kwargs)
        self.g = gclib.py()
        self.address = address
        self.open()

    def open(self) -> None:
        """
        Open connection to Galil motion controller. This method assumes that
        the initial mapping of Galil motion controller's hardware's mapping
        to an IP address is done using GDK and the IP address in burned in.
        This applies that Motion controller no more requests for an IP address
        and a connection to the Motion controller can be done by the IP
        address burned in.
        """
        self.g.GOpen(self.address + ' --direct -s ALL')

    def get_idn(self) -> Dict[str, Optional[str]]:
        """
        Get Galil motion controller hardware information
        """
        data = self.g.GInfo().split(" ")
        idparts: List[Optional[str]] = ["Galil Motion Control, Inc.",
                                        data[1], data[4], data[3][:-1]]

        return dict(zip(('vendor', 'model', 'serial', 'firmware'), idparts))

    def write_raw(self, cmd: str) -> None:
        """
        Write for Galil motion controller
        """
        self.g.GCommand(cmd+"\r")

    def ask_raw(self, cmd: str) -> str:
        """
        Asks/Reads data from Galil motion controller
        """
        return self.g.GCommand(cmd+"\r")

    def timeout(self, val: int) -> None:
        """
        Sets timeout for the instrument

        Args:
            val: time in milliseconds
        """
        if val < 1:
            raise RuntimeError("Timeout can not be less than 1 ms")

        self.g.GTimeout(val)

    def close(self) -> None:
        """
        Close connection to the instrument
        """
        self.g.GClose()


class VectorMode(InstrumentChannel):
    """
    Class to control motors independently
    """

    def __init__(self,
                 parent: "DMC4133Controller",
                 name: str,
                 **kwargs: Any) -> None:
        super().__init__(parent, name, **kwargs)
        self._available_planes = ["AB", "BC", "AC"]

        self.add_parameter("coordinate_system",
                           get_cmd="CA ?",
                           get_parser=self._parse_coordinate_system_active,
                           set_cmd="CA {}",
                           vals=Enum("S", "T"),
                           docstring="sets coordinate system for the motion")

        self.add_parameter("clear_sequence",
                           get_cmd=None,
                           set_cmd="CS {}",
                           vals=Enum("S", "T"),
                           docstring="clears vectors specified in the given "
                                     "coordinate system")

        self.add_parameter("vector_mode_plane",
                           get_cmd=None,
                           set_cmd="VM {}",
                           vals=Enum(*self._available_planes),
                           docstring="sets plane of motion for the motors")

        self.add_parameter("vec_pos_first_coordinate",
                           unit="quadrature counts",
                           vals=Ints(-2147483648, 2147483647),
                           parameter_class=GroupParameter,
                           docstring="sets vector position for plane's first"
                                     "axis. e.g., if vector_mode_plane "
                                     "is specified 'AC'. this param sets "
                                     "vector position for 'A' axis to be used"
                                     "in motion")

        self.add_parameter("vec_pos_second_coordinate",
                           unit="quadrature counts",
                           vals=Ints(-2147483648, 2147483647),
                           parameter_class=GroupParameter,
                           docstring="sets vector position for plane's second"
                                     "axis. e.g., if vector_mode_plane "
                                     "is specified 'AC'. this param sets "
                                     "vector position for 'C' axis to be used"
                                     "in motion")

        self._vector_position = Group([self.vec_pos_first_coordinate,
                                       self.vec_pos_second_coordinate],
                                      set_cmd="VP {vec_pos_first_coordinate},"
                                              "{vec_pos_second_coordinate}",
                                      get_cmd=None)

        self.add_parameter("vector_acceleration",
                           get_cmd="VA ?",
                           get_parser=int,
                           set_cmd="VA {}",
                           vals=Ints(1024, 1073740800),
                           unit="counts/sec2",
                           docstring="sets and gets the defined vector's "
                                     "acceleration")

        self.add_parameter("vector_deceleration",
                           get_cmd="VD ?",
                           get_parser=int,
                           set_cmd="VD {}",
                           vals=Ints(1024, 1073740800),
                           unit="counts/sec2",
                           docstring="sets and gets the defined vector's "
                                     "deceleration")

        self.add_parameter("vector_speed",
                           get_cmd="VS ?",
                           get_parser=int,
                           set_cmd="VS {}",
                           vals=Ints(2, 15000000),
                           unit="counts/sec",
                           docstring="sets and gets defined vector's speed")

    @staticmethod
    def _parse_coordinate_system_active(val: str) -> str:
        """
        parses the the current active coordinate system
        """
        if int(val):
            return "T"
        else:
            return "S"

    def vector_seq_end(self) -> None:
        """
        indicates to the controller that the end of the vector is coming up.
        is required to exit the vector mode gracefully
        """
        self.write("VE")

    def begin_seq(self) -> None:
        """
        begins motion of the motor
        """
        self.write("BG S")

    def after_seq_motion(self) -> None:
        """
        wait till motion ends
        """
        self.write("AM S")


class Motor(InstrumentChannel):
    """
    Class to control motors independently
    """

    def __init__(self,
                 parent: "DMC4133Controller",
                 name: str,
                 **kwargs: Any) -> None:
        super().__init__(parent, name, **kwargs)
        self._axis = name

        self.add_parameter("relative_position",
                           unit="quadrature counts",
                           get_cmd=f"MG _PR{self._axis}",
                           get_parser=int,
                           set_cmd=self._set_relative_position,
                           vals=Ints(-2147483648, 2147483647),
                           docstring="sets relative position for the motor's "
                                     "move")

        self.add_parameter("speed",
                           unit="counts/sec",
                           get_cmd=f"MG _SP{self._axis}",
                           get_parser=int,
                           set_cmd=self._set_speed,
                           vals=Ints(0, 3000000),
                           docstring="speed for motor's motion")

        self.add_parameter("acceleration",
                           unit="counts/sec2",
                           get_cmd=f"MG _AC{self._axis}",
                           get_parser=int,
                           set_cmd=self._set_acceleration,
                           vals=Ints(1024, 1073740800),
                           docstring="acceleration for motor's motion")

        self.add_parameter("deceleration",
                           unit="counts/sec2",
                           get_cmd=f"MG _DC{self._axis}",
                           get_parser=int,
                           set_cmd=self._set_deceleration,
                           vals=Ints(1024, 1073740800),
                           docstring="deceleration for motor's motion")

        self.add_parameter("homing_velocity",
                           unit="counts/sec",
                           get_cmd=f"MG _HV{self._axis}",
                           get_parser=int,
                           set_cmd=self._set_homing_velocity,
                           vals=Ints(0, 3000000),
                           docstring="sets the slew speed for the FI "
                                     "final move to the index and all but the "
                                     "first stage of HM (home)")

        self.add_parameter("off_when_error_occurs",
                           get_cmd=f"MG _OE{self._axis}",
                           set_cmd=self._set_off_when_error_occurs,
                           vals={"disable": 0,
                                 "enable for position, amplifier error or "
                                 "abort input": 1,
                                 "enable for hardware limit switch": 2,
                                 "enable for all": 3},
                           docstring="enables or disables the motor to "
                                     "automatically turn off when error occurs")

        self.add_parameter(
            "stepper_position_maintenance_mode",
            get_cmd=self._stepper_position_maintenance_mode_status,
            set_cmd=self._enable_disable_spm_mode,
            vals={"enable": 1,
                  "disable": 0},
            docstring="enables, disables and gives status of error in SPM mode")

    def _enable_disable_spm_mode(self, val: str) -> None:
        """
        enables/disables Stepper Position Maintenance mode and allows for error
        correction when error happens
        """
        if val == "1":
            self.off_when_error_occurs("enable for position, amplifier error "
                                       "or abort input")
            self._setup_spm()
            self.servo_here()  # Enable axis
            self.root_instrument.wait(50)  # Allow slight settle time
            self.write(f"YS{self._axis}={val}")
        else:
            self.write(f"YS{self._axis}={val}")
            self.off_when_error_occurs("disable")

    def _stepper_position_maintenance_mode_status(self) -> str:
        """
        gives the status if the motor is in SPM mode enabled, disabled or an
        error has occurred. if error has occurred status is received,
        then error can be cleared by setting
        `stepper_position_maintenance_mode` to enable.
        """
        val = self.ask(f"MG _YS{self._axis}")
        if val[0] == "0":
            return "SPM mode disabled"
        elif val[0] == "1":
            return "SPM mode enabled and no error has occurred"
        else:
            return "Error Occurred"

    def _set_off_when_error_occurs(self, val: str) -> None:
        """
        sets the motor to turn off automatically when the error occurs
        """
        self.write(f"OE{self.axis}={val}")

    def _set_homing_velocity(self, val: str) -> None:
        """
        sets the slew speed for the FI final move to the index and all but
        the first stage of HM.
        """
        self.write(f"HV{self._axis}={val}")

    def _set_deceleration(self, val: str) -> None:
        """
        set deceleration for the motor's motion
        """
        self.write(f"DC{self._axis}={val}")

    def _set_acceleration(self, val: str) -> None:
        """
        set acceleration for the motor's motion
        """
        self.write(f"AC{self._axis}={val}")

    def _set_speed(self, val: str) -> None:
        """
        sets speed for motor's motion
        """
        self.write(f"SP{self._axis}={val}")

    def _set_relative_position(self, val: str) -> None:
        """
        sets relative position
        """
        self.write(f"PR{self._axis}={val}")

    def _setup_spm(self) -> None:
        """
        sets up for Stepper Position Maintenance (SPM) mode
        """
        # Set the profiler to stop axis upon error
        self.write(f"KS{self._axis}=16")  # Set step smoothing
        self.write(f"MT{self._axis}=-2")  # Motor type set to stepper
        self.write(f"YA{self._axis}=64")   # Step resolution of the drive

        # Motor resolution (full steps per revolution)
        self.write(f"YB{self._axis}=200")
        # Encoder resolution (counts per revolution)
        self.write(f"YC{self._axis}=4000")

    def off(self) -> None:
        """
        turns motor off
        """
        self.write(f"MO {self._axis}")

    def on_off_status(self) -> str:
        """
        tells motor on off status
        """
        val = self.ask(f"MG _MO{self._axis}")
        if val[0] == "1":
            return "off"
        else:
            return "on"

    def servo_here(self) -> None:
        """
        servo at the motor
        """
        self.write(f"SH {self._axis}")

    def begin(self) -> None:
        """
        begins motion of the motor
        """
        self.write(f"BG {self._axis}")

    def home(self) -> None:
        """
        performs a three stage homing sequence for servo systems and a two
        stage sequence for stepper motor.

         Step One. Servos and Steppers
            - During the first stage of the homing sequence, the motor moves at
            the user-programmed speed until detecting a transition on the
            homing input for that axis. The speed for step one is set with the
            SP command.

            - The direction for this first stage is determined by the
            initial state of the homing input. The state of the homing input
            can be configured using the second field of the CN command.

            - Once the homing input changes state, the motor decelerates to a
            stop.

        Step Two. Servos and Steppers
            - At the second stage, the motor changes directions and
            approaches the transition again at the speed set with the
            HV command. When the transition is detected, the motor is stopped
            instantaneously.

        Step Three. Servos only
            - At the third stage, the motor moves in the positive direction
            at the speed set with the HV command until it detects an index
            pulse via latch from the encoder. It returns to the latched
            position and defines it as position 0.
        """
        # setup for homing
        self.speed(2000)
        self.homing_velocity(256)

        # home command
        self.write(f"HM {self._axis}")

        # begin motion
        self.begin()

    def error_magnitude(self) -> float:
        """
        gives the magnitude of error, in drive step counts, for axes in
        Stepper Position Maintenance mode.

        a step count is directly proportional to the micro-stepping
        resolution of the stepper drive.
        """
        return float(self.ask(f"QS{self._axis}=?"))

    def correct_error(self) -> None:
        """
        this allows the user to correct for position error in Stepper Position
        Maintenance mode and after correction sets
        `stepper_position_maintenance_mode` back to enable
        """
        self.write(f"YR{self._axis}=_QS{self._axis}")
        self.stepper_position_maintenance_mode()


class DMC4133Controller(GalilMotionController):
    """
    Driver for Galil DMC-4133 Controller
    """

    def __init__(self,
                 name: str,
                 address: str,
                 **kwargs: Any) -> None:
        super().__init__(name=name, address=address, **kwargs)

        self.add_parameter("position_format_decimals",
                           get_cmd=None,
                           set_cmd="PF 10.{}",
                           vals=Ints(0, 4),
                           docstring="sets number of decimals in the format "
                                     "of the position")

        self.add_parameter("absolute_position",
                           get_cmd=self._get_absolute_position,
                           set_cmd=None,
                           unit="quadrature counts",
                           docstring="gets absolute position of the motors "
                                     "from the set origin")

        self.add_parameter("wait",
                           get_cmd=None,
                           set_cmd="WT {}",
                           unit="ms",
                           vals=Ints(2, 2147483646),
                           docstring="controller will wait for the amount of "
                                     "time specified before executing the next "
                                     "command")

        self._set_default_update_time()
        self.add_submodule("motor_a", Motor(self, "A"))
        self.add_submodule("motor_b", Motor(self, "B"))
        self.add_submodule("motor_c", Motor(self, "C"))
        self.add_submodule("vector_mode", VectorMode(self, "vector_mode"))

        self.connect_message()

    def _set_default_update_time(self) -> None:
        """
        sets sampling period to default value of 1000. sampling period affects
        the AC, AS, AT, DC, FA, FV, HV, JG, KP, NB, NF, NZ, PL, SD, SP, VA,
        VD, VS, WT commands.
        """
        self.write("TM 1000")

    def _get_absolute_position(self) -> Dict[str, int]:
        """
        gets absolution position of the motors from the defined origin
        """
        result = dict()
        data = self.ask("PA ?,?,?").split(" ")
        result["A"] = int(data[0][:-1])
        result["B"] = int(data[1][:-1])
        result["C"] = int(data[2])

        return result

    def end_program(self) -> None:
        """
        ends the program
        """
        self.write("EN")

    def define_position_as_origin(self) -> None:
        """
        defines current motors position as origin
        """
        self.write("DP 0,0,0")

    def tell_error(self) -> str:
        """
        reads error
        """
        return self.ask("TC1")

    def stop(self) -> None:
        """
        stop the motion of all motors
        """
        self.write("ST")

    def abort(self) -> None:
        """
        aborts motion and the program operation
        """
        self.write("AB")

    def motors_off(self) -> None:
        """
        turn all motors off
        """
        self.write("MO")

    def begin_motors(self) -> None:
        """
        begin motion of all motors simultaneously
        """
        self.write("BG")


class Arm:

    def __init__(self,
                 controller: DMC4133Controller,
                 chip: Dict[str, Union[int, float]],
                 distance: int) -> None:

        self._chip = self._validate_chip_info(chip)
        self._distance = distance
        self.controller = controller
        self._arm_head_status: Optional[str] = None
        self._current_row_num: Optional[int] = None
        self._end_pos: Tuple[int, int, int]

    @staticmethod
    def _validate_chip_info(
            chip: Dict[str, Union[int, float]]
    ) -> Dict[str, Union[int, float]]:
        """
        Assumption here is that, chip design has parallel rows of
        terminals
                    ---------------------
                    | ----------------- |
                    | ----------------- |
                    | ----------------- |
                    | ----------------- |
                    ---------------------
        """

        for key in ["rows", "num_terminals_in_row", "terminal_length",
                    "terminal_width",
                    "inter_terminal_distance_for_adjacent_rows"]:
            if key not in list(chip.keys()):
                raise RuntimeError(f"Chip {key} data not present in the chip "
                                   f"dictionary. Chip dictionary should have "
                                   f"following entries: rows, "
                                   f"num_terminals_in_row, terminal_length, "
                                   f"terminal_width, "
                                   f"inter_terminal_distance_for_adjacent_rows")

        return chip

    def _move(self,
              plane: str,
              rel_first_pos: int,
              rel_second_pos: int) -> None:

        self.controller.vector_mode.vector_mode_plane(plane)
        self.controller.vector_mode.vec_pos_first_coordinate(rel_first_pos)
        self.controller.vector_mode.vec_pos_second_coordinate(rel_second_pos)
        self.controller.vector_mode.vector_acceleration(100000)
        self.controller.vector_mode.vector_speed(2000)
        self.controller.vector_mode.vector_deceleration(100000)
        self.controller.vector_mode.begin_seq()
        self.controller.wait(5000)
        self.controller.vector_mode.vector_seq_end()

    def set_current_position_as_begin(self) -> None:

        assert self._arm_head_status == "down"
        self.controller.define_position_as_origin()

    def set_current_position_as_end(self) -> None:

        assert self._arm_head_status == "down"
        pos = self.controller.absolute_position()
        self._end_pos = (pos["A"], pos["B"], pos["C"])

    def move_arm_head_to_begin(self) -> None:
        """
        Moves arm head from current position to begin position
        """

        assert self._arm_head_status == "down"
        self.lift_arm_head_up(self._distance)
        assert self._arm_head_status == "up"
        self.move_arm_towards_begin()
        assert self._arm_head_status == "up"
        self.align_x_axis((0, 0, 0))
        assert self._arm_head_status == "up"
        self.put_arm_head_down((0, 0, 0))
        assert self._arm_head_status == "down"

    def lift_arm_head_up(self, d: int) -> None:

        curr_pos = self.controller.absolute_position()
        y = curr_pos["B"]
        z = curr_pos["C"]

        m = -1*(y/z)
        denominator = np.sqrt(1+pow(m, 2))

        rel_y1 = -1*d/denominator
        rel_z1 = -1*m*d/denominator

        y1 = y + rel_y1
        z1 = z + rel_z1

        self._move("BC", rel_y1, rel_z1)

        curr_pos = self.controller.absolute_position()
        assert curr_pos["B"] == y1
        assert curr_pos["C"] == z1

        self._arm_head_status = "up"

    def move_arm_towards_begin(self) -> None:

        y = self._end_pos[1]
        z = self._end_pos[2]

        m = z / y
        denominator = np.sqrt(1 + pow(m, 2))
        d = np.sqrt(pow(y, 2) + pow(z, 2))

        rel_y2 = -1 * d / denominator
        rel_z2 = -1 * m * d / denominator

        curr_pos = self.controller.absolute_position()
        y1 = curr_pos["B"]
        z1 = curr_pos["C"]

        y2 = y1 + rel_y2
        z2 = z1 + rel_z2

        self._move("BC", rel_y2, rel_z2)

        curr_pos = self.controller.absolute_position()
        assert curr_pos["B"] == y2
        assert curr_pos["C"] == z2

        self._current_row_num = 0

        self._arm_head_status = "up"

    def move_arm_towards_next_row(self) -> Tuple[int, int, int]:

        x = self._end_pos[0]
        y = self._end_pos[1]
        z = self._end_pos[2]

        d = np.sqrt(x^2 + y^2 + z^2)
        dd = self._chip[
                 "inter_terminal_distance_for_adjacent_rows"
             ]*(self._current_row_num+1)

        x_coord_of_next_row = x*dd/d

        sin_theta = x/d

        distance = dd * np.sqrt(1-np.pow(sin_theta, 2))

        m = z / y
        denominator = np.sqrt(1 + pow(m, 2))

        rel_y2 = -1 * distance / denominator
        rel_z2 = -1 * m * distance / denominator

        curr_pos = self.controller.absolute_position()
        y1 = curr_pos["B"]
        z1 = curr_pos["C"]

        y2 = y1 + rel_y2
        z2 = z1 + rel_z2

        self._move("BC", rel_y2, rel_z2)

        curr_pos = self.controller.absolute_position()
        assert curr_pos["B"] == y2
        assert curr_pos["C"] == z2

        self._current_row_num += 1

        self._arm_head_status = "up"

        return x_coord_of_next_row, y2, z2

    def align_x_axis(self, coord: Tuple[int, int, int]) -> None:

        curr_pos = self.controller.absolute_position()
        x2 = curr_pos["A"]
        y2 = curr_pos["B"]
        z2 = curr_pos["C"]

        rel_x3 = coord[0]-1*x2
        rel_y3 = coord[1]

        self._move("AB", rel_x3, rel_y3)

        curr_pos = self.controller.absolute_position()
        assert curr_pos["A"] == coord[0]
        assert curr_pos["B"] == y2
        assert curr_pos["C"] == z2

        self._arm_head_status = "up"

    def put_arm_head_down(self, coord: Tuple[int, int, int]) -> None:

        curr_pos = self.controller.absolute_position()
        y3 = curr_pos["B"]
        z3 = curr_pos["C"]

        rel_begin_y = coord[1]-1*y3
        rel_begin_z = coord[2]-1*z3

        self._move("BC", rel_begin_y, rel_begin_z)

        curr_pos = self.controller.absolute_position()
        assert curr_pos["A"] == coord[0]
        assert curr_pos["B"] == coord[1]
        assert curr_pos["C"] == coord[2]

        self._arm_head_status = "down"

    def move_arm_head_to_next_row(self) -> None:
        """
        Moves arm head from current position to next row position
        """
        assert self._arm_head_status == "down"
        self.lift_arm_head_up(self._distance)
        assert self._arm_head_status == "up"
        next_row_coord = self.move_arm_towards_next_row()
        assert self._arm_head_status == "up"
        self.align_x_axis(next_row_coord)
        assert self._arm_head_status == "up"
        self.put_arm_head_down(next_row_coord)
        assert self._arm_head_status == "down"
