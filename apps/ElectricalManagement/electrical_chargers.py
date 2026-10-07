from __future__ import annotations

import math
from typing import Iterable, Optional

from electrical_cars import Car, UNAVAIL
from utils import cancel_timer_handler, cancel_listen_handler

from registry import Registry
from guest import GuestManager, DEFAULT_GUEST_REMOVE_AFTER_MINUTES

# Easee: resume resends at 60 s while 'ready_to_charge' before backing off to EASEE_READY_BACKOFF_SECONDS.
EASEE_READY_RESENDS_AT_60S = 10
EASEE_READY_BACKOFF_SECONDS = 600
# Easee: minutes a zero `_current` sample must hold while 'charging' before ampereCharging is set to 0.
EASEE_ZERO_CURRENT_MINUTES = 3

class Charger:

    def __init__(self, api,
        namespace:str,
        charger:str,
        charger_id:str,
        charger_data,
        charging_scheduler,
        notify_app,
        recipients,
    ):

        self.manager = api
        self.ADapi = api.ADapi
        self.connected_vehicle: Optional[Car] = None
        self.namespace = namespace
        self.charger = charger
        self.charger_id = charger_id
        self.charger_data = charger_data
        self.charging_scheduler = charging_scheduler
        self.notify_app = notify_app
        self.recipients = recipients

        # Helpers
        self.checkCharging_handler = None
        self.doNotStartMe:bool = False
        self._recheck_findCarConnectedToCharger_handler = None
        self.reason_for_no_current_handler = None
        self.session_start_charge:float = 0.0

        Registry.register_charger(self)

        # Switch to allow guest to charge: the GuestManager owns the guest car, the switch listener
        # and the create / tear-down sequence (see guest.py).
        self.guest_manager: Optional[GuestManager] = None
        if isinstance(charger_data.guest, str):
            self.guest_manager = GuestManager(api = api, charger = self)
            self.guestCharging = self.guest_manager.switch_is_on
        else:
            self.guestCharging = False

        # Switch to allow current when preheating
        if isinstance(charger_data.idle_current, str):
            self.idle_current = self.ADapi.get_state(charger_data.idle_current, namespace = namespace) == 'on'
            self.ADapi.listen_state(self.idle_currentListen, charger_data.idle_current,
                namespace = namespace
            )
        else:
            self.idle_current = False

        if self.charger_data.charging_amps is not None:
            self.ADapi.listen_state(self.updateAmpereCharging, self.charger_data.charging_amps,
                namespace = namespace
            )

        """ End initialization Charger Class """

    @property
    def _guest_car(self) -> Optional[Car]:
        """ The guest car of this charger, or None. Kept for callers that read it directly. """
        return self.guest_manager.guest if self.guest_manager is not None else None

    def findCarConnectedToCharger(self) -> bool:
        """ A check to see if a car is connected to the charger """

        if self.getChargingState() in ('Disconnected', 'Complete', 'NoPower'):
            return False

        for car in self._cars:
            if not car._polling_of_data() or not car.isConnected():
                continue

            car_state = car.getCarChargerState()
            if car.connected_charger is None or car_state == 'NoPower':
                if (
                    self._link_evidence_ok(car_state)
                    and self.compareChargingState(car_status = car_state)
                ):
                    Registry.set_link(car, self)
                    self.kWhRemaining()
                    self.connected_vehicle.findNewChargeTime()
                    self._register_battery_soc_for_calculation()
                    self.ADapi.log(f"Connected {car.carName} to {self.charger}") ###
                    return True

        if self.connected_vehicle is None:
            if cancel_timer_handler(ADapi = self.ADapi, handler = self._recheck_findCarConnectedToCharger_handler, name = self.charger):
                self._recheck_findCarConnectedToCharger_handler = self.ADapi.run_in(self._recheck_findCarConnectedToCharger, 120)
        return False

    def _link_evidence_ok(self, car_state) -> bool:
        """ Whether a car state is evidence enough to link the car to this charger in
            findCarConnectedToCharger. An unlinked car used to read as False there and could never
            match; now it reads its own state, so this fence keeps the old reachable outcomes:
            a None state (no reading) is never evidence. Easee tightens it to 'NoPower' only. """

        return car_state is not None

    def _recheck_findCarConnectedToCharger(self, kwargs) -> None:
        self.findCarConnectedToCharger()

    def kWhRemaining(self) -> float:
        """ Calculates kWh remaining to charge from car battery sensor/size and charge limit.
            If those are not available it uses session energy to estimate how much is needed to charge """

        chargingState = self.getChargingState()
        if chargingState in ('Complete', 'Disconnected'):
            if self.guestCharging and self.connected_vehicle is not None:
                self.connected_vehicle.car_data.kWh_remain_to_charge = -1
            return -1

        if self.connected_vehicle is not None:
            kWhRemain:float = self.connected_vehicle.kWhRemaining()
            if kWhRemain > -2:
                return kWhRemain

            if self.charger_data.session_energy:
                if self.guestCharging:
                    kWh_remain = self.connected_vehicle.car_data.kWh_remain_to_charge - (float(self.ADapi.get_state(self.charger_data.session_energy, namespace = self.namespace)))
                    if kWh_remain > 2:
                        return kWh_remain
                    else:
                        return 10

                self.connected_vehicle.car_data.kWh_remain_to_charge = self.connected_vehicle.car_data.max_kWh_charged - float(self.ADapi.get_state(self.charger_data.session_energy,
                    namespace = self.namespace)
                )
                return self.connected_vehicle.car_data.kWh_remain_to_charge
        
        return -1

    def compareChargingState(self, car_status:str) -> bool:
        """ Returns True if car and charger match charging state """

        charger_status = self.getChargingState()
        return car_status == charger_status

    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Valid returns: 'Complete' / None / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower' """

        if self.charger_data.charger_sensor is not None:
            if self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace) == 'on':
                # Connected
                if self.charger_data.charger_switch is not None:
                    if self.ADapi.get_state(self.charger_data.charger_switch, namespace = self.namespace) == 'on':
                        return 'Charging'
                    elif self.connected_vehicle is not None and self.connected_vehicle.car_data.kWh_remain_to_charge > 0:
                        return 'Stopped'
                    else:
                        return "Complete"
                return 'Stopped'
            return 'Disconnected'
        return None

    def getChargerPower(self) -> float:
        """ Returns charger power in kWh """

        pwr = self.ADapi.get_state(self.charger_data.charger_power, namespace = self.namespace)
        try:
            pwr = float(pwr)
        except (ValueError, TypeError) as ve:
            self.ADapi.log(f"{self.charger} Could not get charger_power: {pwr} Error: {ve}", level = 'DEBUG')
            pwr = 0
        return pwr

    def getChargingPowerW(self) -> float:
        """ Watt the charger is assumed to draw right now, as used by the core when it stops chargers
            for the hourly cap: ampere * voltPhase. Chargers with a live power sensor override this. """

        return self.charger_data.ampereCharging * self.charger_data.voltPhase

    def setVolts(self) -> None:
        """ Learn volts from charger sensors. Default: keep the configured/persisted value. """
        pass

    def setPhases(self) -> None:
        """ Learn phases from charger sensors. Default: keep the configured/persisted value. """
        pass

    def getmaxChargingAmps(self) -> int:
        """ Returns the maximum ampere the car/charger can get/deliver """

        if self.charger_data.maxChargerAmpere == 0:
            return 32
        
        return self.charger_data.maxChargerAmpere

    def updateAmpereCharging(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates the charging ampere value in self.ampereCharging from charging_amps sensor """

        try:
            newAmp = math.floor(float(new))
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} Not able to get ampere charging. New is {new}. Error {ve}",
                level = 'DEBUG'
            )
        else:
            self.charger_data.ampereCharging = newAmp

    def update_ampere_charging_from_sensor(self) -> int:
        newAmp:int = 0
        try:
            newAmp = math.floor(float(self.ADapi.get_state(self.charger_data.charging_amps,
                                namespace = self.namespace)))
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} Not able to get ampere charging. New is {newAmp}. Error {ve}",
                level = 'DEBUG'
            )
        else:
            self.charger_data.ampereCharging = newAmp
        return newAmp

    def changeChargingAmps(self, charging_amp_change:int = 0) -> None:
        """ Function to change ampere charging +/- """

        if charging_amp_change != 0:
            new_charging_amp = self.charger_data.ampereCharging + charging_amp_change
            self.setChargingAmps(charging_amp_set = new_charging_amp)

    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value. Returns actual restricted within min/max ampere """

        max_available_amps = self.getmaxChargingAmps()
        if charging_amp_set < self.charger_data.min_ampere:
            charging_amp_set = self.charger_data.min_ampere
        elif charging_amp_set > max_available_amps:
            charging_amp_set = max_available_amps
            onboard_charger = getattr(self.connected_vehicle, "onboard_charger", None)
            if onboard_charger is not None:
                connected_charger = getattr(self.connected_vehicle, "connected_charger", None)
                if connected_charger is not onboard_charger:
                    onboard_charger.setChargingAmps(charging_amp_set = onboard_charger.getmaxChargingAmps())

        self._apply_charging_amps(charging_amp_set)
        return charging_amp_set

    def _apply_charging_amps(self, amps:int) -> None:
        """ Sends the ampere to the charger. Child classes override this to use their own API. """

        self.charger_data.ampereCharging = amps
        self.ADapi.call_service('number/set_value',
            value = self.charger_data.ampereCharging,
            entity_id = self.charger_data.charging_amps,
            namespace = self.namespace
        )

    def Charger_ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Function that reacts to charger_sensor connected or disconnected. """

        cancel_listen_handler(ADapi = self.ADapi, handler = self.noPowerDetected_handler, name = self.charger)
        self.noPowerDetected_handler = None

        if self.connected_vehicle is None:
            if not self.findCarConnectedToCharger():
                return

        if (
            self.connected_vehicle.isConnected()
            and new == 'on'
            and self.kWhRemaining() > 0
        ):
            if self.getChargingState() != 'NoPower':
                # Listen for changes made from other connected chargers
                self.noPowerDetected_handler = self.ADapi.listen_state(self.noPowerDetected, self.charger_data.charger_sensor,
                    namespace = self.namespace,
                    attribute = 'charging_state',
                    new = 'NoPower'
                )

                self.connected_vehicle.findNewChargeTime()

            elif self.getChargingState() == 'NoPower':
                self.setChargingAmps(charging_amp_set = self.getmaxChargingAmps())

    def noPowerDetected(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts when chargecable is connected but no power is given.
            This indicates that a smart connected charger has cut the power. """

        connected_charger = getattr(self.connected_vehicle, "connected_charger", None)
        if connected_charger is self:
            Registry.unlink_by_charger(self)

    def ChargingStarted(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger started charging. Check if controlling car and if chargetime has been set up """

        if self.connected_vehicle is None:
            if not self.findCarConnectedToCharger():
                return

        if self.connected_vehicle.pct_start_charge == 100:
            self._register_battery_soc_for_calculation()

        if self.connected_vehicle.isConnected():
            if not self.connected_vehicle.charging_scheduled_with_updated_data():
                self.kWhRemaining()
                self.connected_vehicle.findNewChargeTime()

            elif not self.charging_scheduler.isChargingTime(vehicle_id = self.connected_vehicle.vehicle_id):
                self.stopCharging()

            else:
                self.setVolts()
                self.setPhases()
                self.setVoltPhase(
                    volts = self.charger_data.volts,
                    phases = self.charger_data.phases
                )

    def ChargingStopped(self, entity, attribute, old, new, kwargs) -> None:
        """ Charger stopped. """

        connected_charger = getattr(self.connected_vehicle, "connected_charger", None)
        if connected_charger is self:
            self.setChargingAmps(charging_amp_set = self.charger_data.min_ampere) # Set to minimum amp for preheat.

    # Child classes that must send the stop command even when the charger does not report
    # 'Charging' or 'Starting' (Tesla, Easee, Audi) set this to True.
    SEND_STOP_WHEN_NOT_CHARGING:bool = False

    def startCharging(self) -> None:
        """ Starts charger. Does the bookkeeping and sends the command with _send_start_command.
            The command is repeated by _check_that_charging_started until charging is reported.
            Child classes override _send_start_command, not this method. """

        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger):
            self.checkCharging_handler = None
        if self.doNotStartMe:
            return
        self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_started, 60)

        self.charging_scheduler.markAsCharging(self.connected_vehicle.vehicle_id)
        self._send_start_command()

    def stopCharging(self, force_stop:bool = False) -> None:
        """ Stops charger. Does the bookkeeping and sends the command with _send_stop_command.
            Child classes override _send_stop_command, not this method. """

        if self.connected_vehicle is not None:
            if not self.connected_vehicle.isConnected() or (self.connected_vehicle.dontStopMeNow() and not force_stop):
                return

        cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
        is_charging = self.getChargingState() in ('Charging', 'Starting')
        if is_charging:
            self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_stopped, 60)
        if is_charging or self.SEND_STOP_WHEN_NOT_CHARGING:
            self._send_stop_command()

    def _send_start_command(self) -> None:
        """ Sends the command that starts charging. Override in child classes. """

        self.ADapi.call_service('switch/turn_on',
            entity_id = self.charger_data.charger_switch,
            namespace = self.namespace,
        )

    def _send_stop_command(self) -> None:
        """ Sends the command that stops charging. Override in child classes. """

        self.ADapi.call_service('switch/turn_off',
            entity_id = self.charger_data.charger_switch,
            namespace = self.namespace,
        )

    def _check_that_charging_started(self, kwargs) -> None:
        """ Repeats the start command every 60 seconds until charging is reported.
            The repeat also wakes up cars that sleep and update slowly. """

        cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
        state = self.getChargingState()
        # Deliberately no UNAVAIL filter here: the repeated command is also what wakes a sleeping car
        # whose integration reports 'unavailable'/'unknown'.
        if not state in ('Charging', 'Complete', 'Disconnected'):
            self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_started, 60)
            self._send_start_command()

    def _check_that_charging_stopped(self, kwargs) -> None:
        """ Repeats the stop command every 60 seconds while the charger reports 'Charging'. """

        if self.connected_vehicle is not None:
            cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
            if self.connected_vehicle.dontStopMeNow():
                return
            if self.getChargingState() == 'Charging':
                self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_stopped, 60)
                self._send_stop_command()

    def _updateMaxkWhCharged(self, session: float) -> None:
        if getattr(self.connected_vehicle, 'is_guest', False):
            # A guest session says nothing about the car that normally charges here.
            return
        if self.connected_vehicle.car_data.max_kWh_charged < session:
            self.connected_vehicle.car_data.max_kWh_charged = session

    def _register_battery_soc_for_calculation(self) -> None:
        if (
            self.charger_data.session_energy is not None
            and self.connected_vehicle.car_data.battery_sensor is not None
        ):
            try:
                session = float(self.ADapi.get_state(self.charger_data.session_energy, namespace = self.namespace))
                soc = float(self.ADapi.get_state(self.connected_vehicle.car_data.battery_sensor, namespace = self.namespace))
            except (ValueError, TypeError):
                return
            if session < 4 or self.connected_vehicle.pct_start_charge == 100:
                self.connected_vehicle.pct_start_charge = soc
                self.session_start_charge = session

    def _calculateBatterySize(self, session: float) -> None:
        battery_sensor = getattr(self.connected_vehicle.car_data, 'battery_sensor', None)
        battery_reg_counter = getattr(self.connected_vehicle.car_data, 'battery_reg_counter', 0)

        if battery_sensor is not None:
            try:
                soc_now = float(self.ADapi.get_state(battery_sensor, namespace = self.namespace))
            except (ValueError, TypeError):
                return
            # Percent minus percent, and kWh minus kWh (the kWh already in the session when the
            # start SOC was registered). Mixing the two units gave a battery size that was off by
            # session_start_charge percentage points.
            pctCharged = soc_now - self.connected_vehicle.pct_start_charge
            session_kWh = session - self.session_start_charge

            if pctCharged > 35:
                self._updateBatterySize(session_kWh, pctCharged, battery_reg_counter)
            elif pctCharged > 10 and self.connected_vehicle.car_data.battery_size == 100 and battery_reg_counter == 0:
                self.connected_vehicle.car_data.battery_size = (session_kWh / pctCharged)*100

    def _updateBatterySize(self, session: float, pctCharged: float, battery_reg_counter: int) -> None:
        if battery_reg_counter == 0:
            avg = round((session / pctCharged) * 100, 2)
        else:
            avg = round(
                ((self.connected_vehicle.car_data.battery_size * battery_reg_counter) + (session / pctCharged) * 100)
                / (battery_reg_counter + 1),
                2
            )

        self.connected_vehicle.car_data.battery_reg_counter += 1

        if self.connected_vehicle.car_data.battery_reg_counter > 100:
            self.connected_vehicle.car_data.battery_reg_counter = 10

        self.connected_vehicle.car_data.battery_size = avg

    def _CleanUpWhenChargingStopped(self) -> None:
        if self.connected_vehicle is not None:
            connected_charger = getattr(self.connected_vehicle, "connected_charger", None)
            if connected_charger is self:
                if self.getChargingState() in ('Complete', 'Disconnected'):
                    self.connected_vehicle._handleChargeCompletion()
                    if self.charger_data.session_energy and self.connected_vehicle.pct_start_charge < 90:
                        session = float(self.ADapi.get_state(self.charger_data.session_energy, namespace=self.namespace))
                        self._updateMaxkWhCharged(session)
                        self._calculateBatterySize(session)

                    self.connected_vehicle.pct_start_charge = 100
                    self.session_start_charge = 0
        self.charger_data.ampereCharging = 0
        cancel_listen_handler(ADapi = self.ADapi, handler = self.reason_for_no_current_handler, name = "reason for no current")
        self.reason_for_no_current_handler = None

    def setVoltPhase(self, volts, phases) -> None:
        """ Helper for calculations on chargespeed.
            VoltPhase is a make up name and simplification to calculate chargetime based on remaining kwh to charge
            230v 1 phase,
            266v is 3 phase on 230v without neutral (supported by tesla among others)
            687v is 3 phase on 400v with neutral """

        if (
            phases > 1
            and self.charger_data.volts > 200
            and self.charger_data.volts < 250
        ):
            self.charger_data.voltPhase = 266

        elif (
            phases == 3
            and self.charger_data.volts > 300
        ):
            self.charger_data.voltPhase = 687

        elif (
            phases == 1
            and self.charger_data.volts > 200
            and self.charger_data.volts < 250
        ):
            self.charger_data.voltPhase = volts

    def idle_currentListen(self, entity, attribute, old, new, kwargs) -> None:
        if new == 'on':
            self.idle_current = True
        elif new == 'off':
            self.idle_current = False

    def notify_charge_now_or_kWhRemain(self, carName):
        """ Sends notification to ask to charge car Now or input kWh remaining """

        data = {
            'tag' : carName,
            'actions' : [{ 'action' : 'chargeNow'+str(self.charger), 'title' : f'Charge {carName} Now' },
                         { 'action' : 'kWhremaining'+str(self.charger),
                           'title' : 'Input expected kWh to charge',
                           "behavior": "textInput"
                           } ]
            }
        self.notify_app.send_notification(
                    message = f"Guest Car connected. Select options.",
                    message_title = f"{self.charger}",
                    message_recipient = self.recipients,
                    also_if_not_home = True,
                    data = data
                )

    # ---- guest: thin delegates to the GuestManager (guest.py) ---------------------------- #

    def guestChargingListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Guest switch change. The GuestManager registers its own listener; this stays for
            callers that fire the switch change directly. """

        if self.guest_manager is not None:
            self.guest_manager.switch_changed(entity, attribute, old, new, kwargs)

    def _addGuestCar(self):
        """ Creates the guest car (idempotent). """

        if self.guest_manager is not None:
            self.guest_manager.begin_guest(reason = 'guest car requested', notify = False)

    def setGuestKWh(self, kWh:float) -> None:
        """ Sets kWh to charge for the guest car and remembers it for the next guest. """

        if self.guest_manager is None:
            self.ADapi.log(f"{self.charger}: kWh {kWh} received for a guest car but the charger has no guest switch. Ignored.", level = 'INFO')
            return
        self.guest_manager.set_kWh(kWh)

class Tesla_charger(Charger):
    """ Tesla
        Child class of Charger. Uses Tesla custom integration. https://github.com/alandtse/tesla Easiest installation is via HACS. """

    def __init__(self, api,
        Car,
        namespace:str,
        charger:str,
        charger_data,
        charging_scheduler,
        notify_app,
        recipients,
    ):

        charger_id = api.ADapi.get_state(Car.car_data.online_sensor,
            namespace = Car.namespace,
            attribute = 'id'
        )
        if charger_id is None:
            # Same fallback as Tesla_car.vehicle_id so car and onboard charger stay paired and two
            # Teslas never collide on None in the Registry.
            api.ADapi.log(
                f"{charger}: no 'id' attribute on {Car.car_data.online_sensor}. Using '{charger}' as charger id.",
                level = 'ERROR'
            )
            charger_id = charger

        self._cars:list = [Car]

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_id = charger_id,
            charger_data = charger_data,
            charging_scheduler = charging_scheduler,
            notify_app = notify_app,
            recipients = recipients,
        )

        self.noPowerDetected_handler = None

        Registry.set_onboard_link(Car, self)

        self.ADapi.listen_state(self.ChargingStarted, self.charger_data.charger_switch,
            namespace = self.namespace,
            new = 'on',
            duration = 10
        )
        self.ADapi.listen_state(self.ChargingStopped, self.charger_data.charger_switch,
            namespace = self.namespace,
            new = 'off'
        )
        self.ADapi.listen_state(self.Charger_ChargeCableConnected, self.charger_data.charger_sensor,
            namespace = self.namespace
        )

        self.ADapi.listen_state(self.MaxAmpereChanged, self.charger_data.charging_amps,
            namespace = self.namespace,
            attribute = 'max',
            duration = 30
        )
        """ End initialization Tesla Charger Class """

    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'. """

        try:
            state = self.ADapi.get_state(self.charger_data.charger_sensor,
                namespace = self.namespace,
                attribute = 'charging_state'
            )
            if state == 'Starting':
                state = 'Charging'
        except (ValueError, TypeError) as ve:
            return None
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not get attribute = 'charging_state' from: "
                f"{self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace)} "
                f"Exception: {e}",
                level = 'WARNING'
            )
            return None
        # Set as connected charger if restarted after cable connected.
        connected_charger = getattr(self.connected_vehicle, "connected_charger", None)
        if (
            state == 'Stopped' and
            connected_charger is None
        ):
            Registry.set_link(self._cars[0], self)

        return state

    def _apply_charging_amps(self, amps:int) -> None:
        self.charger_data.ampereCharging = amps
        self.ADapi.call_service('tesla_custom/api',
            namespace = self.namespace,
            command = 'CHARGING_AMPS',
            parameters = {'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': self.charger_data.ampereCharging}
        )

    def MaxAmpereChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Detects if smart charger (Easee) increases ampere available to charge and updates internal charger to follow. """

        if new is None or new in UNAVAIL:
            return
        try:
            new_max = int(math.ceil(float(new)))
            chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.charging_amps,
                namespace = self.namespace))
            )
            connected_charger = getattr(self.connected_vehicle, "connected_charger", None)
            if float(new) > chargingAmpere:
                if (
                    connected_charger is not self and
                    connected_charger is not None
                ):
                    self.setChargingAmps(charging_amp_set = self.getmaxChargingAmps())

        except (ValueError, TypeError):
            pass
        else:
            # maxChargerAmpere is an int: storing the raw attribute string made the next
            # 'int > str' comparison in setChargingAmps raise TypeError.
            if new_max > self.charger_data.maxChargerAmpere:
                self.charger_data.maxChargerAmpere = new_max

    SEND_STOP_WHEN_NOT_CHARGING = True

    def _send_start_command(self) -> None:
        self.ADapi.create_task(self.start_Tesla_charging())

    async def start_Tesla_charging(self):
        if self.connected_vehicle is not None:
            try:
                await self.ADapi.call_service('tesla_custom/api',
                    namespace = self.namespace,
                    command = 'START_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
                await self.connected_vehicle._force_API_update()
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception: {e}", level = 'WARNING')

    def _send_stop_command(self) -> None:
        self.ADapi.create_task(self.stop_Tesla_charging())

    async def stop_Tesla_charging(self):
        try:
            await self.ADapi.call_service('tesla_custom/api',
                namespace = self.namespace,
                command = 'STOP_CHARGE',
                parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
            )
            await self.connected_vehicle._force_API_update()
        except Exception as e:
            self.ADapi.log(f"{self.charger} Could not Stop Charging: {e}", level = 'WARNING')

    def _check_that_charging_started(self, kwargs) -> None:
        connected_charger = getattr(self.connected_vehicle, "connected_charger", None)
        if (
            self.getChargingState() == 'NoPower'
            and connected_charger is self
        ):
            Registry.unlink_by_charger(self)
        else:
            super()._check_that_charging_started(kwargs)

    def setVolts(self):
        if self.connected_vehicle.isConnected():
            try:
                volt = math.ceil(float(self.ADapi.get_state(self.charger_data.charger_power,
                namespace = self.namespace,
                attribute = 'charger_volts'))
            )
            except (ValueError, TypeError):
                pass
            else:
                if volt > 0:
                    self.charger_data.volts = volt

    def setPhases(self):
        if self.connected_vehicle.isConnected():
            try:
                phase = int(self.ADapi.get_state(self.charger_data.charger_power,
                namespace = self.namespace,
                attribute = 'charger_phases')
            )
            except (ValueError, TypeError):
                pass
            else:
                if phase > 0:
                    self.charger_data.phases = phase


class Easee(Charger):
    """ Easee
        Child class of Charger. Uses Easee EV charger component for Home Assistant. https://github.com/nordicopen/easee_hass 
        Easiest installation is via HACS. """

    def __init__(self, api,
        cars: Iterable[Car],
        namespace:str,
        charger:str,
        charger_data,
        charging_scheduler,
        notify_app,
        recipients,
        guest_remove_after_minutes:int = DEFAULT_GUEST_REMOVE_AFTER_MINUTES,
    ):

        charger_id:str = api.ADapi.get_state(charger_data.charger_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        self._cars:list = cars
        # Seconds the Easee must report 'disconnected' before _check_if_still_disconnected acts
        # (guest session ended, or a Tesla relinked to its onboard charger). Config only, not persisted.
        self._disconnected_check_seconds:int = max(1, int(guest_remove_after_minutes)) * 60

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_id = charger_id,
            charger_data = charger_data,
            charging_scheduler = charging_scheduler,
            notify_app = notify_app,
            recipients = recipients,
        )

        # Minumum ampere if locked to 3 phase
        if self.charger_data.phases == 3:
            self.charger_data.min_ampere = 11

        self._check_if_still_disconnected_handler = None

        # C7: resume commands sent by _check_that_charging_started while the Easee stays in
        # 'ready_to_charge' (plugged, car not drawing). Reset on any status change.
        self._ready_resend_count:int = 0
        # C9: consecutive zero/unavailable `_current` samples while the Easee reports 'charging',
        # one minute apart. Reset on any non-zero sample. Handle for the one-minute re-sample timer.
        self._zero_current_samples:int = 0
        self._zero_current_handler = None

        self.ADapi.listen_state(self.statusChange, self.charger_data.charger_sensor, namespace = namespace)

        """ End initialization Easee Charger Class """

    def _link_evidence_ok(self, car_state) -> bool:
        """ The Easee links a car only on the evidence that was reachable before an unlinked car
            could report its own state: the car says 'NoPower' (cable in, EVSE gives nothing) and
            compareChargingState confirms the Easee is 'awaiting_start'. 'Charging' / 'Stopped' /
            'Complete' from a Tesla are not evidence for the Easee: two Teslas on cloud data up to
            11 min late could otherwise be linked to the wrong charger. """

        return car_state == 'NoPower'

    def compareChargingState(self, car_status:str) -> bool:
        """ Returns True if car and charger match charging state. """

        charger_status = self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace)
        if charger_status == 'charging':
            return car_status == 'Charging'
        elif charger_status == 'completed':
            return car_status == 'Complete'
        elif charger_status == 'awaiting_start':
            return car_status == 'NoPower'
        elif charger_status == 'disconnected':
            return car_status == 'Disconnected'

        return False

    def getChargingState(self) -> str:
        """ Returns the charging state of the charger.
            Easee state can be: 'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'. """

        status = self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace)
        if status == 'charging':
            return 'Charging'
        elif status == 'completed':
            return 'Complete'
        elif status == 'awaiting_start':
            return 'awaiting_start'
        elif status == 'disconnected':
            if self.connected_vehicle is not None:
                return 'awaiting_start'
            return 'Disconnected'
        elif not status == 'ready_to_charge':
            self.ADapi.log(f"Status: {status} for {self.charger} is not defined", level = 'WARNING')
        return status

    def statusChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to changes in state of the charger.
            Easee state can be: 'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status """

        if new != 'ready_to_charge':
            # C7: the state changed (or charging started): the ready_to_charge resend counter starts over.
            self._ready_resend_count = 0

        if old == 'disconnected':
            if self.connected_vehicle is None:
                if self.findCarConnectedToCharger():
                    if self.connected_vehicle is not None:
                        self.kWhRemaining() # Update kWh remaining to charge
                        self.connected_vehicle.findNewChargeTime()
                        return
            return

        elif (
            new != 'disconnected'
            and old == 'completed'
        ):
            if self.connected_vehicle is not None:
                if (
                    self.kWhRemaining() > 2
                    and not self.connected_vehicle.charging_scheduled_with_updated_data()
                ):
                    self.connected_vehicle.findNewChargeTime()

                if (
                    self.charging_scheduler.isChargingTime(vehicle_id = self.connected_vehicle.vehicle_id)
                    or self.idle_current # Preheating
                ):
                    return

            self.stopCharging()

        elif (
            new == 'charging'
            or new == 'ready_to_charge'
        ):
            if self.connected_vehicle is None:
                if not self.findCarConnectedToCharger():
                    self.stopCharging()
                    return
            if self.connected_vehicle is not None:
                if not self.connected_vehicle.charging_scheduled_with_updated_data():
                    self.kWhRemaining()
                    self.connected_vehicle.findNewChargeTime()

                elif not self.charging_scheduler.isChargingTime(vehicle_id = self.connected_vehicle.vehicle_id):
                    self.stopCharging()

                else:
                    self.setVolts()
                    self.setPhases()
                    self.setVoltPhase(
                        volts = self.charger_data.volts,
                        phases = self.charger_data.phases
                    )

        elif new == 'completed':
            if self.connected_vehicle is not None:
                self._CleanUpWhenChargingStopped()
                if self._guest_car is not None:
                    self.ADapi.log(f"{self._guest_car.carName} connected to {self.charger} is complete. Check it disconnects properly") ###
                    #self.ADapi.call_service('input_boolean/turn_off',
                    #    entity_id = self.charger_data.guest,
                    #    namespace = self.namespace,
                    #)
        elif new == 'disconnected':
            if cancel_timer_handler(ADapi = self.ADapi, handler = self._check_if_still_disconnected_handler, name = self.charger):
                self._check_if_still_disconnected_handler = None
            self._check_if_still_disconnected_handler = self.ADapi.run_in(self._check_if_still_disconnected, self._disconnected_check_seconds)

        elif new == 'awaiting_start':
            if self.connected_vehicle is None:
                if not self.findCarConnectedToCharger():
                    self.stopCharging()
                    return

    def _check_if_still_disconnected(self, kwargs) -> None:
        """ Runs guest_remove_after_minutes after the Easee reported 'disconnected'. While a vehicle
            is linked the Easee reads 'awaiting_start' instead of 'Disconnected' (getChargingState),
            so this timer is what ends a guest session or relinks a Tesla to its onboard charger. """

        self._check_if_still_disconnected_handler = None
        guest = self._guest_car
        minutes = self._disconnected_check_seconds // 60
        if guest is not None:
            self.ADapi.log(f"{guest.carName} connected to {self.charger} when disconnected.") ###
        else:
            self.ADapi.log(f"No guest car connected to {self.charger} when disconnected.") ###
        if self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace) == 'disconnected':
            if self.connected_vehicle is not None:
                self._CleanUpWhenChargingStopped()
                if self.connected_vehicle is not guest:
                    Registry.relink_to_onboard(self)

            if guest is not None:
                self.ADapi.log(f"{guest.carName} disconnects.") ###
                self.guest_manager.end_guest(reason = f"{self.charger} disconnected for {minutes} minutes")
        elif self.connected_vehicle is not None: # Check if new car is connected.
            if self.connected_vehicle.getCarChargerState() == 'Disconnected':
                self._CleanUpWhenChargingStopped()
                Registry.relink_to_onboard(self)
                self.findCarConnectedToCharger()
            if guest is not None:
                self.ADapi.log(f"{self.charger} was not disconnected {minutes} minutes later while charge guest is on") ###
        elif self.connected_vehicle is None: # New car connected.
            if guest is not None:
                self.ADapi.log(f"{guest.carName} disconnects based on new car connected.") ###
                self.guest_manager.end_guest(reason = f"{self.charger} has no linked vehicle {minutes} minutes after disconnect")
            self.findCarConnectedToCharger()


    def reasonChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to reasonChange in Easee charger.
            Easee reason can be:
            'no_current_request' / 'undefined' / 'waiting_in_queue' / 'limited_by_charger_max_limit' /
            'limited_by_local_adjustment' / 'limited_by_car' / 'car_not_charging' /  from reason_for_no_current """

        if (
            new == 'limited_by_car'
            and self.connected_vehicle is not None
        ):
            try:
                chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.charging_amps,
                    namespace = self.namespace))
                )
            except (ValueError, TypeError):
                return
            if (
                self.connected_vehicle.car_data.car_limit_max_ampere != chargingAmpere
                and chargingAmpere >= 6
            ):
                self.connected_vehicle.car_data.car_limit_max_ampere = chargingAmpere

    def setVolts(self):
        try:
            self.charger_data.volts = math.ceil(float(self.ADapi.get_state(self.charger_data.voltage,
                namespace = self.namespace))
            )
        except (ValueError, TypeError):
            return

    def setPhases(self):
        try:
            self.charger_data.phases = int(self.ADapi.get_state(self.charger_data.charger_sensor,
            namespace = self.namespace,
            attribute = 'config_phaseMode')
        )
        except (ValueError, TypeError):
            self.charger_data.phases = 1
        # Minimum ampere if locked to 3 phase (same rule as in __init__, applied when learned later).
        if self.charger_data.phases == 3:
            self.charger_data.min_ampere = 11

    def _apply_charging_amps(self, amps:int) -> None:
        if (
            self.charger_data.ampereCharging != amps
            and self.charger_data.ampereCharging != amps -1
        ):
            self.ADapi.call_service('easee/set_charger_dynamic_limit',
                namespace = self.namespace,
                current = amps,
                charger_id = self.charger_id
            )

    def findCarConnectedToCharger(self) -> bool:
        if super().findCarConnectedToCharger():
            if (
                self.connected_vehicle is not None
                and self.connected_vehicle.onboard_charger is None
                and self.charger_data.reason_for_no_current is not None
            ):
                # Learn the max ampere the car accepts for cars without an onboard charger (guests).
                cancel_listen_handler(ADapi = self.ADapi, handler = self.reason_for_no_current_handler, name = "reason for no current")
                self.reason_for_no_current_handler = self.ADapi.listen_state(self.reasonChange,
                    self.charger_data.reason_for_no_current,
                    namespace = self.namespace
                )
            return True
        return False

    SEND_STOP_WHEN_NOT_CHARGING = True

    def _send_start_command(self) -> None:
        try:
            self.ADapi.call_service('easee/action_command',
                namespace = self.namespace,
                action_command = 'resume',
                charger_id = self.charger_id
            )
        except Exception as e:
            self.ADapi.log(f"{self.charger} Could not Start Charging. Exception {e}", level = 'WARNING')

    def _send_stop_command(self) -> None:
        try:
            self.ADapi.call_service('easee/action_command',
                namespace = self.namespace,
                action_command = 'pause',
                charger_id = self.charger_id
            )
        except Exception as e:
            self.ADapi.log(f"{self.charger} Could not Stop Charging. Exception: {e}", level = 'WARNING')

    # ---- C7: resume loop back-off in 'ready_to_charge' ---------------------------------- #

    def _check_that_charging_started(self, kwargs) -> None:
        """ Same 60 s resume loop as Charger._check_that_charging_started (same handle, same stop
            states), except in 'ready_to_charge': the cable is in and the Easee offers power but the
            car does not draw (asleep, or stopped in the car app). Resuming the Easee can not change
            that, so after EASEE_READY_RESENDS_AT_60S resends the loop backs off to one resume every
            EASEE_READY_BACKOFF_SECONDS and says so ONCE (WARNING + notification). The car is never
            woken from here. The counter resets when the status changes (statusChange) or when this
            check sees any other state. """

        cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
        state = self.getChargingState()
        if state != 'ready_to_charge':
            self._ready_resend_count = 0
            # Deliberately no UNAVAIL filter here (see Charger._check_that_charging_started).
            if not state in ('Charging', 'Complete', 'Disconnected'):
                self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_started, 60)
                self._send_start_command()
            return

        self._ready_resend_count += 1
        if self._ready_resend_count < EASEE_READY_RESENDS_AT_60S:
            delay = 60
        else:
            delay = EASEE_READY_BACKOFF_SECONDS
            if self._ready_resend_count == EASEE_READY_RESENDS_AT_60S:
                self._warn_still_ready_to_charge()
        self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_started, delay)
        self._send_start_command()
        self.ADapi.log(f"{self.charger} ready_to_charge resume {self._ready_resend_count}, next in {delay} s ###", level = 'DEBUG')

    def _warn_still_ready_to_charge(self) -> None:
        car = self.connected_vehicle.carName if self.connected_vehicle is not None else 'no car'
        message = (
            f"Easee {self.charger} still ready_to_charge after {self._ready_resend_count} resume commands; "
            f"car ({car}) may be asleep or stopped in the car app. Resume is now sent every "
            f"{EASEE_READY_BACKOFF_SECONDS // 60} minutes."
        )
        self.ADapi.log(message, level = 'WARNING')
        try:
            self.notify_app.send_notification(
                message = message,
                message_title = f"🚘Charging {self.charger}",
                message_recipient = self.recipients,
                also_if_not_home = True,
                data = {'tag': 'charging' + str(self.charger)}
            )
        except Exception as e:
            self.ADapi.log(f"{self.charger} could not send notification: {e}", level = 'DEBUG')

    # ---- C9: hold ampereCharging over momentary zero `_current` samples ------------------ #

    def updateAmpereCharging(self, entity, attribute, old, new, kwargs) -> None:
        """ `charging_amps` is the Easee `_current` sensor: the ACTUAL draw, not a set limit. """

        self._sample_ampere(new)

    def update_ampere_charging_from_sensor(self) -> int:
        return self._sample_ampere(self.ADapi.get_state(self.charger_data.charging_amps, namespace = self.namespace))

    def _easee_reports_charging(self) -> bool:
        return self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace) == 'charging'

    def _sample_ampere(self, raw) -> int:
        """ Stores one `_current` sample in charger_data.ampereCharging and returns the value in use.

            A momentary 0 (or unavailable) while the Easee reports 'charging' used to set
            ampereCharging to 0: isChargingAtMaxAmps went false and the increase path then set the
            dynamic limit to min_ampere + a few amps, cutting a 32 A limit to 6-10 A. Now a zero
            sample while charging keeps the last non-zero value and re-samples every 60 s; the zero
            is accepted once it has held for EASEE_ZERO_CURRENT_MINUTES consecutive minutes. Any
            non-zero sample resets the counter. When the Easee is not charging a parsable sample
            is stored as before (0 included); an unparsable one keeps the old value as before. """

        try:
            newAmp = math.floor(float(raw))
            parsable = True
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} Not able to get ampere charging. New is {raw}. Error {ve}",
                level = 'DEBUG'
            )
            newAmp = 0
            parsable = False

        if newAmp > 0 or not self._easee_reports_charging():
            self._reset_zero_current_samples()
            if parsable:
                self.charger_data.ampereCharging = newAmp
            return self.charger_data.ampereCharging

        # Easee says charging, sample says 0 / unavailable.
        self._zero_current_samples += 1
        if self._zero_current_samples > EASEE_ZERO_CURRENT_MINUTES:
            self._reset_zero_current_samples()
            self.ADapi.log(
                f"{self.charger} current 0 for {EASEE_ZERO_CURRENT_MINUTES} minutes while charging; ampereCharging "
                f"{self.charger_data.ampereCharging} -> 0 ###",
                level = 'DEBUG'
            )
            self.charger_data.ampereCharging = 0
            return 0

        self.ADapi.log(
            f"{self.charger} zero current sample {self._zero_current_samples}/{EASEE_ZERO_CURRENT_MINUTES + 1} while charging; "
            f"keeping ampereCharging {self.charger_data.ampereCharging} ###",
            level = 'DEBUG'
        )
        if cancel_timer_handler(ADapi = self.ADapi, handler = self._zero_current_handler, name = self.charger):
            self._zero_current_handler = None
        self._zero_current_handler = self.ADapi.run_in(self._resample_zero_current, 60)
        return self.charger_data.ampereCharging

    def _reset_zero_current_samples(self) -> None:
        self._zero_current_samples = 0
        if cancel_timer_handler(ADapi = self.ADapi, handler = self._zero_current_handler, name = self.charger):
            self._zero_current_handler = None

    def _resample_zero_current(self, kwargs) -> None:
        self._zero_current_handler = None
        self.update_ampere_charging_from_sensor()

class Onboard_charger(Charger):
    """ Child class of Charger used for onboard for Car. """

    def __init__(self, api,
        Car,
        namespace:str,
        charger:str,
        charger_id:str,
        charger_data,
        charging_scheduler,
        notify_app,
        recipients,
    ):

        self._cars:list = [Car]

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_id = charger_id,
            charger_data = charger_data,
            charging_scheduler = charging_scheduler,
            notify_app = notify_app,
            recipients = recipients,
        )

        self.setVoltPhase(volts = charger_data.volts,
                          phases = charger_data.phases)

        self.noPowerDetected_handler = None
        Registry.set_onboard_link(Car, self)

        self.ADapi.listen_state(self.ChargingStarted, self.charger_data.charger_switch,
            namespace = self.namespace,
            new = 'on',
            duration = 10
        )
        self.ADapi.listen_state(self.ChargingStopped, self.charger_data.charger_switch,
            namespace = self.namespace,
            new = 'off'
        )
        self.ADapi.listen_state(self.Charger_ChargeCableConnected, self.charger_data.charger_sensor,
            namespace = self.namespace
        )

# --------------------------------------------------------------------------- #
# Audi Connect charging_state normalisation. ONE table for the whole app.
#
# UNVERIFIED: the audiconnect vehicle model (the file that produces the charging_state
# strings) was not available; the values below follow the VW-group/Cariad API names used by
# other integrations. Comparison is case-insensitive. A value that is not in the table is
# reported ONCE with a WARNING and treated as "no transition" (previous state kept), so a
# wrong guess here never sends a command, it only logs.
# --------------------------------------------------------------------------- #
AUDI_STATE_EXACT: dict[str, str] = {
    'charging':          'Charging',
    'readyforcharging':  'Stopped',
    'conservation':      'Complete',
    'error':             'Stopped',    # + WARNING once
}
AUDI_STATE_PREFIX: tuple[tuple[str, str], ...] = (
    ('chargepurposereached', 'Complete'),   # chargePurposeReached_conservation / _notConservationCharging ...
)
AUDI_STATE_PLUG_DEPENDENT: tuple[str, ...] = ('notreadyforcharging',)


def normalise_audi_charging_state(raw, plug_on: bool, previous: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """ Maps a raw audiconnect charging_state to the app's states.

        Returns (state, warning). `state` is one of 'Charging' / 'Stopped' / 'Complete' /
        'Disconnected' / 'NoPower' or `previous` when the raw value is unavailable/unknown/None or
        not in the table (no transition). `warning` is a message the caller should log once. """

    if raw is None:
        return previous, None
    key = str(raw).strip().lower()
    if key in UNAVAIL or key == '':
        return previous, None
    if key in AUDI_STATE_PLUG_DEPENDENT:
        # Car reports it can not charge: cable out -> Disconnected, cable in -> the EVSE gives no power.
        return ('NoPower' if plug_on else 'Disconnected'), None
    if key in AUDI_STATE_EXACT:
        warning = f"charging_state is '{raw}' (car reports a charging error). Treated as Stopped." if key == 'error' else None
        return AUDI_STATE_EXACT[key], warning
    for prefix, state in AUDI_STATE_PREFIX:
        if key.startswith(prefix):
            return state, None
    return previous, f"Unknown charging_state '{raw}'. Add it to AUDI_STATE_EXACT in electrical_chargers.py. Keeping state '{previous}'."


class Audi_charger(Charger):
    """ Audi Connect
        Child class of Charger. Uses Audi Connect custom integration https://github.com/audiconnect/audi_connect_ha.
        Easiest installation is via HACS.

        NEVER RAN ON A REAL CAR. What is unverified is marked in comments. Differences from the Tesla:
        * the integration polls every 15 minutes, so there is no 60 s resend loop. One command, ONE verify
          timer of `verify_minutes`, at most `max_command_retries` resends, then WARNING + notification.
        * start/stop go through the `audiconnect/execute_vehicle_action` service which can block for
          ~15 s while the integration confirms the command; it is called with `callback=` so the app
          thread does not wait.
        * no ampere control: the Audi charges at whatever the EVSE gives. `charge_power_kW` (optional)
          gives the scheduler a realistic power estimate. """

    SEND_STOP_WHEN_NOT_CHARGING = True
    AUDI_ACTION_SERVICE = 'audiconnect/execute_vehicle_action'

    def __init__(self, api,
        Car,
        namespace:str,
        charger:str,
        vehicle_id,
        charger_data,
        charging_scheduler,
        notify_app,
        recipients,
        device_id:Optional[str] = None,
        verify_minutes:float = 20,
        max_command_retries:int = 2,
        charge_power_kW:Optional[float] = None,
    ):

        self._cars:list = [Car]

        super().__init__(
            api = api,
            namespace = namespace,
            charger = charger,
            charger_id = vehicle_id,
            charger_data = charger_data,
            charging_scheduler = charging_scheduler,
            notify_app = notify_app,
            recipients = recipients,
        )

        # Config only (not persisted). services.yaml declares `device_id` (HA device id) as the
        # required target; `vin` is kept as the fallback the old code used.
        self.device_id:Optional[str] = device_id
        self.verify_minutes:float = verify_minutes
        self.max_command_retries:int = max_command_retries
        self.charge_power_kW:Optional[float] = charge_power_kW

        # Command intent: 'start' / 'stop' / None. Set when a command is sent, cleared when the
        # state sensor confirms it or when the retries are used up.
        self._command_intent:Optional[str] = None
        self._command_retries:int = 0
        self._last_state:Optional[str] = None
        self._warned_states:set = set()
        self._warned_no_device_id:bool = False

        if self.charge_power_kW:
            # Estimate from the configured charger power: maxAmps = P / voltPhase. No ampere control,
            # so min = max = the estimate.
            self.setVoltPhase(volts = charger_data.volts, phases = charger_data.phases)
            amps = max(1, int(round(float(self.charge_power_kW) * 1000 / self.charger_data.voltPhase)))
            self.charger_data.min_ampere = amps
            self.charger_data.ampereCharging = amps
            self.charger_data.maxChargerAmpere = amps
        else:
            ### SET DEFAULT VALUES (old behaviour when charge_power_kW is not configured):
            self.charger_data.voltPhase = 230
            self.charger_data.min_ampere = 16
            self.charger_data.ampereCharging = 16
            self.charger_data.maxChargerAmpere = 16
            ###

        self.noPowerDetected_handler = None

        Registry.set_onboard_link(Car, self)

        # charger_sensor is the text sensor `sensor.<car>_charging_state` (UNVERIFIED entity id, derived
        # from the "Charging state" entity name in audiconnect/sensor.py). ChargingStarted/Stopped fire on
        # the NORMALISED state, not on 'on'/'off'.
        if self.charger_data.charger_sensor is not None:
            self.ADapi.listen_state(self._charging_state_changed, self.charger_data.charger_sensor,
                namespace = self.namespace
            )
        # charger_switch is the plug binary_sensor `binary_sensor.<car>_plug_state` (UNVERIFIED entity id,
        # "Plug state" in audiconnect/binary_sensor.py). It is read-only; it only tells if the cable is in.
        if self.charger_data.charger_switch is not None:
            self.ADapi.listen_state(self.Charger_ChargeCableConnected, self.charger_data.charger_switch,
                namespace = self.namespace
            )

        """ End initialization Audi Charger Class """

    # ---- state -------------------------------------------------------------------------- #

    def _plug_connected(self) -> bool:
        if self.charger_data.charger_switch is None:
            return False
        return self.ADapi.get_state(self.charger_data.charger_switch, namespace = self.namespace) == 'on'

    def _raw_charging_state(self):
        if self.charger_data.charger_sensor is None:
            return None
        return self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace)

    def getChargingState(self) -> Optional[str]:
        """ Returns the normalised charging state of the car's onboard charger.
            Valid returns: 'Complete' / None / 'Stopped' / 'Charging' / 'Disconnected' / 'NoPower'.
            unavailable/unknown/None and unknown strings return the previous state (no transition). """

        raw = self._raw_charging_state()
        state, warning = normalise_audi_charging_state(raw, self._plug_connected(), self._last_state)
        if warning is not None and warning not in self._warned_states:
            self._warned_states.add(warning)
            self.ADapi.log(f"{self.charger}: {warning}", level = 'WARNING')
        if state != self._last_state:
            self.ADapi.log(f"{self.charger} charging_state '{raw}' -> {state}", level = 'DEBUG')
            self._last_state = state

        # Set as connected charger if restarted after cable connected (same rule as Tesla_charger).
        # _cars[0] is the car itself: never None, unlike connected_vehicle after an unlink.
        car = self._cars[0]
        if state == 'Stopped' and car.connected_charger is None:
            Registry.set_link(car, self)

        return state

    def _charging_state_changed(self, entity, attribute, old, new, kwargs) -> None:
        """ Listener on the charging_state sensor. Maps to the normalised state, confirms a pending
            command and fires ChargingStarted / ChargingStopped on real transitions. """

        previous = self._last_state
        state = self.getChargingState()
        if state == previous:
            return
        self._reconcile_intent(state)
        if state == 'Charging':
            self.ChargingStarted(entity, attribute, old, new, kwargs)
        elif previous == 'Charging':
            self.ChargingStopped(entity, attribute, old, new, kwargs)

    # ---- commands (capped, verified) ---------------------------------------------------- #

    def _intent_satisfied(self, intent:Optional[str], state:Optional[str]) -> bool:
        if state is None:
            return False
        if intent == 'start':
            return state in ('Charging', 'Complete', 'Disconnected')
        if intent == 'stop':
            return state != 'Charging'
        return True

    def _reconcile_intent(self, state:Optional[str]) -> None:
        """ Clears the pending command when the sensor confirms it. """

        if self._command_intent is not None and self._intent_satisfied(self._command_intent, state):
            self.ADapi.log(f"{self.charger} {self._command_intent} confirmed, state {state}") ###
            self._clear_intent()

    def _clear_intent(self) -> None:
        self._command_intent = None
        self._command_retries = 0
        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger):
            self.checkCharging_handler = None

    def _command_pending(self, intent:str) -> bool:
        return (
            self._command_intent == intent
            and self.checkCharging_handler is not None
            and self.ADapi.timer_running(self.checkCharging_handler)
        )

    def _vehicle_for_commands(self):
        return self.connected_vehicle if self.connected_vehicle is not None else self._cars[0]

    def startCharging(self) -> None:
        """ Sends start_charger once and verifies after `verify_minutes`. A start that is still being
            verified is not sent again (the queue runner calls this every minute). """

        if self.doNotStartMe:
            return
        if self._command_pending('start'):
            self.ADapi.log(f"{self.charger} start already sent, waiting for the car to report", level = 'DEBUG')
            return
        self._begin_command('start')

    def stopCharging(self, force_stop:bool = False) -> None:
        if self.connected_vehicle is not None:
            if not self.connected_vehicle.isConnected() or (self.connected_vehicle.dontStopMeNow() and not force_stop):
                return
        if self._command_pending('stop'):
            return
        self._begin_command('stop')

    def _begin_command(self, intent:str) -> None:
        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger):
            self.checkCharging_handler = None
        self._command_intent = intent
        self._command_retries = 0
        if intent == 'start':
            self.charging_scheduler.markAsCharging(self._vehicle_for_commands().vehicle_id)
            self._send_start_command()
        else:
            self._send_stop_command()
        self.checkCharging_handler = self.ADapi.run_in(self._verify_command, int(self.verify_minutes * 60))

    def _verify_command(self, kwargs) -> None:
        """ Runs `verify_minutes` after a command. Resends at most `max_command_retries` times. """

        self.checkCharging_handler = None
        intent = self._command_intent
        if intent is None:
            return
        raw = self._raw_charging_state()
        state = self.getChargingState()
        if raw is None or str(raw).lower() in UNAVAIL or state is None:
            # Integration outage: no decision, check again after the next poll window.
            self.checkCharging_handler = self.ADapi.run_in(self._verify_command, int(self.verify_minutes * 60))
            return
        if self._intent_satisfied(intent, state):
            self._clear_intent()
            return
        if self._command_retries < self.max_command_retries:
            self._command_retries += 1
            self.ADapi.log(
                f"{self.charger} {intent} not confirmed after {self.verify_minutes} min (state {state}). "
                f"Resend {self._command_retries}/{self.max_command_retries}",
                level = 'INFO'
            )
            if intent == 'start':
                self._send_start_command()
            else:
                self._send_stop_command()
            self.checkCharging_handler = self.ADapi.run_in(self._verify_command, int(self.verify_minutes * 60))
            return

        message = (
            f"{self.charger}: {intent} charging was sent {self.max_command_retries + 1} times but the car still "
            f"reports {state}. Giving up until the next scheduled attempt."
        )
        self.ADapi.log(message, level = 'WARNING')
        try:
            self.notify_app.send_notification(
                message = message,
                message_title = f"🚘Charging {self.charger}",
                message_recipient = self.recipients,
                also_if_not_home = True,
                data = {'tag': 'charging' + str(self.charger)}
            )
        except Exception as e:
            self.ADapi.log(f"{self.charger} could not send notification: {e}", level = 'DEBUG')
        if intent == 'start':
            self.charging_scheduler.removeFromCharging(self._vehicle_for_commands().vehicle_id)
        self._clear_intent()

    def _service_target(self) -> dict:
        if self.device_id:
            return {'device_id': self.device_id}
        if not self._warned_no_device_id:
            self._warned_no_device_id = True
            self.ADapi.log(
                f"{self.charger}: no 'device_id' configured for the audiconnect services. services.yaml requires "
                f"device_id (the Home Assistant device id of the car); falling back to vin={self.charger_id}, "
                "which the service may reject. Add 'device_id' to the audi entry in the configuration.",
                level = 'WARNING'
            )
        return {'vin': self.charger_id}

    def _call_audi_action(self, action:str) -> None:
        """ Calls the service without blocking the app thread: AppDaemon's call_service returns at once when
            a `callback` is given (adapi.call_service, AppDaemon 4.5). The service itself can take ~15 s while
            the integration confirms the command with the car. """

        try:
            self.ADapi.call_service(self.AUDI_ACTION_SERVICE,
                namespace = self.namespace,
                callback = self._audi_action_done,
                action = action,
                **self._service_target()
            )
            self.ADapi.log(f"{action} sent to {self.charger}") ###
        except Exception as e:
            self.ADapi.log(f"{self.charger} Could not send {action}. Exception: {e}", level = 'WARNING')

    def _audi_action_done(self, result) -> None:
        # Runs on AppDaemon's event loop (task done-callback): keep it to a log line.
        self.ADapi.log(f"{self.charger} audiconnect action result: {result}", level = 'DEBUG')

    def _send_start_command(self) -> None:
        self._call_audi_action('start_charger')

    def _send_stop_command(self) -> None:
        self._call_audi_action('stop_charger')

    # ---- capabilities ------------------------------------------------------------------- #

    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ The Audi has no ampere control (audiconnect exposes no current setter). Returns the
            current estimate so callers that use the return value get an int. """

        return int(self.charger_data.ampereCharging)

    def getChargingPowerW(self) -> float:
        """ Live draw from the `charging_power` sensor (kW, UNVERIFIED entity id
            `sensor.<car>_charging_power`) when it reads above 0, else the ampere * voltPhase estimate. """

        if self.charger_data.charger_power is not None:
            power_kW = self.getChargerPower()
            if power_kW > 0:
                return power_kW * 1000
        return super().getChargingPowerW()
