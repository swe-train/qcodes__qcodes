import re
import textwrap
from typing import Optional, TYPE_CHECKING, Tuple, Union

from qcodes.instrument.channel import InstrumentChannel

from .KeysightB1500_module import B1500Module
from .message_builder import MessageBuilder
from . import constants
from .constants import ModuleKind, ChNr
if TYPE_CHECKING:
    from .KeysightB1500 import KeysightB1500


_pattern = re.compile(
    r"((?P<status>\w)(?P<chnr>\w)(?P<dtype>\w))?"
    r"(?P<value>[+-]\d{1,3}\.\d{3,6}E[+-]\d{2})"
)


class B1520A(B1500Module):
    """
    Driver for Keysight B1520A Capacitance Measurement Unit module for B1500
    Semiconductor Parameter Analyzer.

    Args:
        parent: mainframe B1500 instance that this module belongs to
        name: Name of the instrument instance to create. If `None`
            (Default), then the name is autogenerated from the instrument
            class.
        slot_nr: Slot number of this module (not channel number)
    """
    time_out_phase_compensation = 60  # manual says around 30 seconds
    MODULE_KIND = ModuleKind.CMU

    def __init__(self, parent: 'KeysightB1500', name: Optional[str], slot_nr,
                 **kwargs):
        super().__init__(parent, name, slot_nr, **kwargs)

        self.channels = (ChNr(slot_nr),)

        self.add_parameter(
            name="voltage_dc", set_cmd=self._set_voltage_dc, get_cmd=None
        )

        self.add_parameter(
            name="voltage_ac", set_cmd=self._set_voltage_ac, get_cmd=None
        )

        self.add_parameter(
            name="frequency", set_cmd=self._set_frequency, get_cmd=None
        )

        self.add_parameter(name="capacitance",
                           get_cmd=self._get_capacitance,
                           snapshot_value=False)

        self.add_submodule('correction', Correction(self, 'correction'))

        self.add_parameter(name="phase_compensation_mode",
                           set_cmd=self._set_phase_compensation_mode,
                           get_cmd=None,
                           set_parser=constants.ADJ.Mode,
                           get_parser=constants.ADJ.Mode,
                           docstring=textwrap.dedent("""
            This parameter selects the MFCMU phase compensation mode. This
            command initializes the MFCMU. The available modes are captured 
            in :class:`constants.ADJ.Mode`:
 
                - 0: Auto mode. Initial setting.
                - 1: Manual mode.
                - 2: Load adaptive mode.
    
            For mode=0, the KeysightB1500 sets the compensation data 
            automatically. For mode=1, execute the 
            :meth:`phase_compensation` method (the ``ADJ?`` command) to
            perform the phase compensation and set the compensation data. 
            For mode=2, the KeysightB1500 performs the phase compensation 
            before every measurement. It is useful when there are wide load 
            fluctuations by changing the bias and so on."""))

    def _set_voltage_dc(self, value: float) -> None:
        msg = MessageBuilder().dcv(self.channels[0], value)

        self.write(msg.message)

    def _set_voltage_ac(self, value: float) -> None:
        msg = MessageBuilder().acv(self.channels[0], value)

        self.write(msg.message)

    def _set_frequency(self, value: float) -> None:
        msg = MessageBuilder().fc(self.channels[0], value)

        self.write(msg.message)

    def _get_capacitance(self) -> Tuple[float, float]:
        msg = MessageBuilder().tc(
            chnum=self.channels[0], mode=constants.RangingMode.AUTO
        )

        response = self.ask(msg.message)

        parsed = [item for item in re.finditer(_pattern, response)]

        if (
                len(parsed) != 2
                or parsed[0]["dtype"] != "C"
                or parsed[1]["dtype"] != "Y"
        ):
            raise ValueError("Result format not supported.")

        return float(parsed[0]["value"]), float(parsed[1]["value"])

    def _set_phase_compensation_mode(self, mode: constants.ADJ.Mode) -> None:
        msg = MessageBuilder().adj(chnum=self.channels[0], mode=mode)
        self.write(msg.message)

    def phase_compensation(
            self,
            mode: Optional[Union[constants.ADJQuery.Mode, int]] = None
    ) -> constants.ADJQuery.Response:
        with self.root_instrument.timeout.set_to(
                self.time_out_phase_compensation):
            msg = MessageBuilder().adj_query(chnum=self.channels[0],
                                             mode=mode)
            response = self.ask(msg.message)
        return constants.ADJQuery.Response(int(response))

    def abort(self):
        """
        Aborts currently running operation and the subsequent execution.
        This does not abort the timeout process. Only when the kernel is
        free this command is executed and the further commands are aborted.
        """
        msg = MessageBuilder().ab()
        self.write(msg.message)


class Correction(InstrumentChannel):
    """
    A Keysight B1520A CMU submodule for performing open/short/load corrections.
    """

    def __init__(self, parent: 'B1520A', name: str, **kwargs):
        super().__init__(parent=parent, name=name, **kwargs)
        self._chnum = parent.channels[0]

    def enable(self, corr: constants.CalibrationType) -> None:
        """
        This command enables the open/short/load correction. Before enabling a
        correction, perform the corresponding correction data measurement by
        using the :meth:`perform_correction`.

        Args:
            corr: Depending on the the correction you want to perform,
                set this to OPEN, SHORT or LOAD. For ex: In case of open
                correction corr = constants.CalibrationType.OPEN.
        """
        msg = MessageBuilder().corrst(chnum=self._chnum,
                                      corr=corr,
                                      state=True)
        self.write(msg.message)

    def disable(self, corr: constants.CalibrationType) -> None:
        """
        This command disables an open/short/load correction.

        Args:
            corr: Correction type as in :class:`constants.CalibrationType`
        """
        msg = MessageBuilder().corrst(chnum=self._chnum,
                                      corr=corr,
                                      state=False)
        self.write(msg.message)

    def is_enabled(self, corr: constants.CalibrationType
                   ) -> constants.CORRST.Response:
        """
        Query instrument to see if a correction of the given type is
        enabled.

        Args:
            corr: Correction type as in :class:`constants.CalibrationType`
        """
        msg = MessageBuilder().corrst_query(chnum=self._chnum, corr=corr)

        response = self.ask(msg.message)
        return constants.CORRST.Response(int(response))

    def set_reference_value_for_correction(self,
                                           corr: constants.CalibrationType,
                                           mode: constants.DCORR.Mode,
                                           primary: float,
                                           secondary: float):
        """
        This command disables the open/short/load correction function and
        defines the calibration value or the reference value of the
        open/short/load standard. The correction data will be invalid after
        this command.

        Args:
            corr: Correction mode from constants.CalibrationType.
                OPEN for Open correction
                SHORT for Short correction
                LOAD for Load correction.
            mode:  Measurement mode from constants.DCORR.Mode
                Cp-G (for open correction)
                Ls-Rs (for short or load correction).
            primary : Primary reference value of the standard. Cp value for
                the open standard. in F. Ls value for the short or load
                standard. in H.
            secondary : Secondary reference value of the standard. G value
                for the open standard. in S. Rs value for the short or load
                standard. in Ω.
        """

        msg = MessageBuilder().dcorr(chnum=self._chnum,
                                     corr=corr,
                                     mode=mode,
                                     primary=primary,
                                     secondary=secondary)
        self.write(msg.message)

    def get_reference_value_for_correction(self,
                                           corr: constants.CalibrationType):
        """
        This command returns the calibration value or the reference value of
        the open/short/load standard.

        Args:
            corr: Correction mode from constants.CalibrationType.
                OPEN for Open correction
                SHORT for Short correction
                LOAD for Load correction.
            mode:  Measurement mode from constants.DCORR.Mode
                Cp-G (for open correction)
                Ls-Rs (for short or load correction).
        """

        msg = MessageBuilder().dcorr_query(chnum=self._chnum,
                                           corr=corr)
        response = self.ask(msg.message)
        response = response.split(',')
        return f'Mode: {constants.DCORR.Mode(int(response[0])).name}, ' \
               f'Primary (Cp/Ls): {response[1]} in F/H, ' \
               f'Secondary (G/Rs): {response[2]} in S/Ω'

    def clear_frequency_for_correction(self, mode: constants.CLCORR.Mode):
        """
        Remove all frequencies in the list for data correction. Can also
        set the default frequency list.

        Args:
            mode: CLEAR_ONLY if you just want to clear the frequency list.
                 CLEAR_AND_SET_DEFAULT_FREQ is you want to clear the frequency
                 list and set the default frequencies, 1 k, 2 k, 5 k, 10 k,
                  20 k, 50 k, 100 k, 200 k, 500 k, 1 M, 2 M, and 5 MHz.
        """
        msg = MessageBuilder().clcorr(chnum=self._chnum, mode=mode)
        self.write(msg.message)

    def add_frequency_for_correction(self, freq: int):
        """
        Append MFCMU output frequency for data correction in the list.

        Args:
            freq:

        """
        msg = MessageBuilder().corrl(chnum=self._chnum, freq=freq)
        self.write(msg.message)

    def get_frequency_list_for_correction(self, index: Optional[int] = None):
        """
        Get the frequency list for CMU data correction
        """
        msg = MessageBuilder().corrl_query(chnum=self._chnum,
                                           index=index)
        response = self.ask(msg.message)
        return response

    def perform_correction(self, corr: constants.CalibrationType
                           ) -> constants.CORR.Response:
        """
        Perform Open/Short/Load corrections using this method. Refer to the
        example notebook to understand how each of the corrections are
        performed.

        Before executing this command, set the oscillator level of the MFCMU.

        If you use the correction standard, execute the DCORR command before
        this command. The calibration value or the reference value of the
        standard must be defined before executing this command.

        Args:
            corr: Depending on the the correction you want to perform,
                set this to OPEN, SHORT or LOAD. For ex: In case of open
                correction corr = constants.CalibrationType.OPEN .

        Response:
            0: Correction data measurement completed successfully.
            1: Correction data measurement failed.
            2: Correction data measurement aborted.
        """
        msg = MessageBuilder().corr_query(
            chnum=self._chnum,
            corr=corr
        )
        response = self.ask(msg.message)
        return constants.CORR.Response(int(response))

    def perform_and_enable_correction(self,
                                      corr: constants.CalibrationType,
                                      state: bool = True,
                                      ):
        """
        To perform the correction and enable it.

        Perform Open/Short/Load corrections using this method. Refer to the
        example notebook to understand how each of the corrections are
        performed.

        Before executing this command, set the oscillator level of the MFCMU.

        If you use the correction standard, execute the DCORR command before
        this command. The calibration value or the reference value of the
        standard must be defined before executing this command.

        Args:
            corr: Depending on the the correction you want to perform,
                set this to OPEN, SHORT or LOAD. For ex: In case of open
                correction corr = constants.CalibrationType.OPEN .
            state: `True` if you want to enable correction else `False`.
                Default is set to true.

        """
        response_perform_correction = self.perform_correction(corr=corr)
        self.enable(corr=corr, state=state)
        response_enable_correction = self.is_enabled(corr=corr)
        response_out = f'Correction status {response_perform_correction} and ' \
                   f'Enable {response_enable_correction}'
        return response_out

