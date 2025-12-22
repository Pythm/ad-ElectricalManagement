from __future__ import annotations

import math
import inspect
import uuid
from typing import Optional

from electrical_cars import Car
from pydantic_models import CarData
from utils import cancel_timer_handler, cancel_listen_handler

from registry import Registry

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
        self._guest_car = None

        Registry.register_charger(self)

        # Switch to allow guest to charge
        if isinstance(charger_data.guest, str):
            self.guestCharging = self.ADapi.get_state(charger_data.guest, namespace = namespace) == 'on'
            self.ADapi.listen_state(self.guestChargingListen, charger_data.guest,
                namespace = namespace
            )
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


    def findCarConnectedToCharger(self) -> bool:
        """ A check to see if a car is connected to the charger """

        if self.getChargingState() in ('Disconnected', 'Complete', 'NoPower'):
            return False

        for car in self._cars:
            if not car._polling_of_data() or not car.isConnected():
                continue

            if car.connected_charger is None or car.getCarChargerState() == 'NoPower':
                if self.compareChargingState(
                    car_status = car.getCarChargerState()
                ):
                    Registry.set_link(car, self)
                    self.kWhRemaining()
                    self.connected_vehicle.findNewChargeTime()
                    self._register_battery_soc_for_calculation()
                    return True

        if self.connected_vehicle is None:
            if cancel_timer_handler(ADapi = self.ADapi, handler = self._recheck_findCarConnectedToCharger_handler, name = self.charger):
                self._recheck_findCarConnectedToCharger_handler = self.ADapi.run_in(self._recheck_findCarConnectedToCharger, 120)
        return False

    def _recheck_findCarConnectedToCharger(self, kwargs) -> None:
        self.findCarConnectedToCharger()

    def kWhRemaining(self) -> float:
        """ Calculates kWh remaining to charge from car battery sensor/size and charge limit.
            If those are not available it uses session energy to estimate how much is needed to charge """

        chargingState = self.getChargingState()
        if chargingState in ('Complete', 'Disconnected'):
            if self.guestCharging:
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

    def setmaxChargingAmps(self) -> bool:
        """ Set maxChargerAmpere from charger sensors """

        self.charger_data.maxChargerAmpere = 32
        self.ADapi.log(
            f"Setting maxChargerAmpere to 32. Set value in child class of charger.",
            level = 'WARNING'
        )
        return True

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

        stack = inspect.stack() # Check if called from child
        if stack[1].function != 'setChargingAmps':
            self.charger_data.ampereCharging = charging_amp_set
            self.ADapi.call_service('number/set_value',
                value = self.charger_data.ampereCharging,
                entity_id = self.charger_data.charging_amps,
                namespace = self.namespace
            )
        return charging_amp_set

    def Charger_ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Function that reacts to charger_sensor connected or disconnected. """

        if cancel_listen_handler(ADapi = self.ADapi, handler = self.noPowerDetected_handler, name = self.charger):
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

        if  self.connected_vehicle.isConnected():
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

    def startCharging(self) -> bool:
        """ Starts charger. Parent class returns boolen to child if ready to start charging """

        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger):
            self.checkCharging_handler = None
        if self.doNotStartMe:
            return False
        self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_started, 60)

        self.charging_scheduler.markAsCharging(self.connected_vehicle.vehicle_id)
        stack = inspect.stack()
        if stack[1].function == 'startCharging':
            return True
        else:
            self.ADapi.call_service('switch/turn_on',
                entity_id = self.charger_data.charger_switch,
                namespace = self.namespace,
            )
        return False

    def stopCharging(self, force_stop:bool = False) -> bool:
        """ Stops charger. Parent class returns boolen to child if able to stop charging """

        if self.connected_vehicle is not None:
            if not self.connected_vehicle.isConnected() or (self.connected_vehicle.dontStopMeNow() and not force_stop):
                return False

        cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
        if self.getChargingState() in ('Charging', 'Starting'):
            self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_stopped, 60)

            stack = inspect.stack()
            if stack[1].function != 'stopCharging':
                self.ADapi.call_service('switch/turn_off',
                    entity_id = self.charger_data.charger_switch,
                    namespace = self.namespace,
                )
        return True

    def _check_that_charging_started(self, kwargs) -> bool:
        cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
        if not self.getChargingState() in ('Charging', 'Complete', 'Disconnected'):
            self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_started, 60)

            stack = inspect.stack()
            if stack[1].function in ('startCharging', '_check_that_charging_started'):
                return False
            else:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.charger_data.charger_switch,
                    namespace = self.namespace,
                )
        return True

    def _check_that_charging_stopped(self, kwargs) -> bool:
        if self.connected_vehicle is not None:
            cancel_timer_handler(ADapi = self.ADapi, handler = self.checkCharging_handler, name = self.charger)
            if self.connected_vehicle.dontStopMeNow():
                return True
            if self.getChargingState() == 'Charging':
                self.checkCharging_handler = self.ADapi.run_in(self._check_that_charging_stopped, 60)

                stack = inspect.stack()
                if stack[1].function in ('stopCharging', '_check_that_charging_stopped'):
                    return False
                else:
                    self.ADapi.call_service('switch/turn_off',
                        entity_id = self.charger_data.charger_switch,
                        namespace = self.namespace,
                    )

        return True

    def _updateMaxkWhCharged(self, session: float) -> None:
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
            pctCharged = float(self.ADapi.get_state(battery_sensor, namespace = self.namespace)) - self.session_start_charge - self.connected_vehicle.pct_start_charge

            if pctCharged > 35:
                self._updateBatterySize(session, pctCharged, battery_reg_counter)
            elif pctCharged > 10 and self.connected_vehicle.car_data.battery_size == 100 and battery_reg_counter == 0:
                self.connected_vehicle.car_data.battery_size = (session / pctCharged)*100

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
        if cancel_listen_handler(ADapi = self.ADapi, handler = self.reason_for_no_current_handler, name = "reason for no current"):
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

    def guestChargingListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Handles smart chargers when guest connects on HA switch change """

        self.guestCharging = new == 'on'
        if (
            new == 'on'
            and old == 'off'
        ):
            self._addGuestCar()
            self.notify_charge_now_or_kWhRemain(self.connected_vehicle.carName)

        elif (
            new == 'off'
            and old == 'on'
        ):
            if self.connected_vehicle is not None:
                if self.connected_vehicle.vehicle_id == self._guest_car.vehicle_id:
                    self.connected_vehicle._handleChargeCompletion()
                    self.stopCharging()
                    self.remove_car_from_list(self.connected_vehicle.vehicle_id)
                    Registry.unlink_by_charger(self)
                    self._guest_car = None
                elif (
                    self.connected_vehicle.isConnected()
                    and self.kWhRemaining() > 0
                ):
                    self.connected_vehicle.findNewChargeTime()
            else:
                self.stopCharging()

    def _addGuestCar(self):
        """ Create a “dumb” guest car """

        if self._guest_car is not None:
            return
        guest_car_cfg = CarData()
        guest_id = f"guest_{uuid.uuid4().hex[:8]}"

        self._guest_car = Car(
            api = self.ADapi,
            namespace = self.namespace,
            carName = guest_id,
            vehicle_id = guest_id,
            car_data = guest_car_cfg,
            charging_scheduler = self.charging_scheduler,
        )

        self.add_car_to_list(self._guest_car)
        Registry.set_link(self._guest_car, self)
        self.connected_vehicle.car_data.kWh_remain_to_charge = 10

    def add_car_to_list(self, car_instance):
        self.manager.add_car(car_instance)

    def remove_car_from_list(self, vehicle_id):
        self.manager.remove_car(vehicle_id)

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
            Registry.set_link(self.connected_vehicle, self)

        return state

    def setmaxChargingAmps(self) -> bool:
        """ Set maxChargerAmpere from charger sensors. """

        if (
            self.connected_vehicle.isConnected()
            and self.getChargingState() not in ('Disconnected', 'Complete')
        ):
            connected_charger = getattr(self.connected_vehicle, "connected_charger", None)
            if connected_charger is self:
                try:
                    maxAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.charging_amps,
                        namespace = self.namespace,
                        attribute = 'max'))
                    )
                    self.charger_data.maxChargerAmpere = maxAmpere

                except (ValueError, TypeError) as ve:
                    self.ADapi.log(
                        f"{self.charger} Could not get maxChargingAmps. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                    return False

            # Update Voltphase calculations
            try:
                self.charger_data.volts = math.ceil(float(self.ADapi.get_state(self.charger_data.charger_power,
                    namespace = self.namespace,
                    attribute = 'charger_volts'
                )))
            except (ValueError, TypeError):
                pass
            try:
                self.charger_data.phases = int(self.ADapi.get_state(self.charger_data.charger_power,
                    namespace = self.namespace,
                    attribute = 'charger_phases'
                ))
            except (ValueError, TypeError):
                pass
            return True
        return False

    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere. """

        self.charger_data.ampereCharging = super().setChargingAmps(charging_amp_set = charging_amp_set)
        self.ADapi.call_service('tesla_custom/api',
            namespace = self.namespace,
            command = 'CHARGING_AMPS',
            parameters = {'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': self.charger_data.ampereCharging}
        )

    def MaxAmpereChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Detects if smart charger (Easee) increases ampere available to charge and updates internal charger to follow. """

        try:
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
            if float(new) > self.charger_data.maxChargerAmpere:
                self.charger_data.maxChargerAmpere = new

    def startCharging(self) -> None:
        if super().startCharging():
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

    def stopCharging(self, force_stop:bool = False) -> None:
        if super().stopCharging(force_stop = force_stop):
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

        elif not super()._check_that_charging_started(0):
            self.ADapi.create_task(self.start_Tesla_charging())

    def _check_that_charging_stopped(self, kwargs) -> None:
        if not super()._check_that_charging_stopped(0):
            self.ADapi.create_task(self.stop_Tesla_charging())

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
    ):

        charger_id:str = api.ADapi.get_state(charger_data.charger_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        self._cars:list = cars

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

        self.ADapi.listen_state(self.statusChange, self.charger_data.charger_sensor, namespace = namespace)

        """ End initialization Easee Charger Class """

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

        elif new == 'disconnected':
            self.ADapi.run_in(self._check_if_still_disconnected, 720)

        elif new == 'awaiting_start':
            if self.connected_vehicle is None:
                self.findCarConnectedToCharger()

    def _check_if_still_disconnected(self, kwargs) -> None:
        if self.ADapi.get_state(self.charger_data.charger_sensor, namespace = self.namespace) == 'disconnected':
            if self.connected_vehicle is not None:
                self._CleanUpWhenChargingStopped()
                Registry.relink_to_onboard(self)
        elif self.connected_vehicle is not None: # Check if new car is connected.
            if self.connected_vehicle.getCarChargerState() == 'Disconnected':
                self._CleanUpWhenChargingStopped()
                Registry.relink_to_onboard(self)
                self.findCarConnectedToCharger()
        elif self.connected_vehicle is None: # New car connected.
            self.findCarConnectedToCharger()


    def reasonChange(self, entity, attribute, old, new, kwargs) -> None:
        """ Listens to reasonChange in Easee charger.
            Easee reason can be:
            'no_current_request' / 'undefined' / 'waiting_in_queue' / 'limited_by_charger_max_limit' /
            'limited_by_local_adjustment' / 'limited_by_car' / 'car_not_charging' /  from reason_for_no_current """

        if (
            new == 'limited_by_car'
        ):
            chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.charging_amps,
                namespace = self.namespace))
            )
            if (
                self.connected_vehicle.car_data.car_limit_max_ampere != chargingAmpere
                and chargingAmpere >= 6
            ):
                self.connected_vehicle.car_data.car_limit_max_ampere = chargingAmpere

    def setmaxChargingAmps(self) -> bool:
        """ Set maxChargerAmpere from charger sensors """

        try:
            self.charger_data.maxChargerAmpere = math.ceil(float(self.ADapi.get_state(self.charger_data.max_charger_limit,
                namespace = self.namespace))
            )
        except (ValueError, TypeError):
            return False

        return True

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

    def setChargingAmps(self, charging_amp_set:int = 16) -> None:
        """ Function to set ampere charging to received value.
            returns actual restricted within min/max ampere. """

        charging_amp_set = super().setChargingAmps(charging_amp_set = charging_amp_set)
        if (
            self.charger_data.ampereCharging != charging_amp_set
            and self.charger_data.ampereCharging != charging_amp_set -1
        ):
            self.ADapi.call_service('easee/set_charger_dynamic_limit',
                namespace = self.namespace,
                current = charging_amp_set,
                charger_id = self.charger_id
            )

    def findCarConnectedToCharger(self) -> bool:
        if super().findCarConnectedToCharger():
            if self.connected_vehicle.onboard_charger is None:
                # Set max ampere charging for unconnected cars.
                self.reason_for_no_current_handler = self.ADapi.listen_state(self.reasonChange, reason_for_no_current, namespace = namespace)
            return True
        return False

    def startCharging(self) -> None:
        if super().startCharging():
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'resume',
                    charger_id = self.charger_id
                )
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception {e}", level = 'WARNING')

    def stopCharging(self, force_stop:bool = False) -> None:
        if super().stopCharging(force_stop = force_stop):
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'pause',
                    charger_id = self.charger_id
                )
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging. Exception: {e}", level = 'WARNING')

    def _check_that_charging_started(self, kwargs) -> None:
        if not super()._check_that_charging_started(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'resume',
                    charger_id = self.charger_id
                    )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Start Charging in _check_that_charging_started for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )

    def _check_that_charging_stopped(self, kwargs) -> None:
        if not super()._check_that_charging_stopped(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    namespace = self.namespace,
                    action_command = 'pause',
                    charger_id = self.charger_id
                    )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Stop Charging in _check_that_charging_stopped for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )


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
