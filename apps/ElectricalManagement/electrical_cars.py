from __future__ import annotations

import math
import inspect ###
from datetime import timedelta
from typing import Optional

from utils import cancel_timer_handler#, cancel_listen_handler

from registry import Registry
from scheduler import Scheduler

UNAVAIL = ('unavailable', 'unknown')

class Car:
    """ Car parent class
    Set variables in childclass before init:
        self.vehicle_id:str # Unik ID to separate chargers. CarName will be used if not set

    Set variables in childclass after init if needed:
        self.guestCharging:bool # Defaults to False
        self.connected_vehicle # Car to charge
    """
    def __init__(self, api,
        namespace:str,
        carName:str, # Name of car
        vehicle_id:str, # ID of car
        car_data,
        charging_scheduler,
    ):

        self.ADapi = api
        self.namespace = namespace
        self.car_data = car_data
        self.charging_scheduler = charging_scheduler

        self.vehicle_id = vehicle_id
        self.carName = carName

        # Set up when car should be finished charging
        if isinstance(car_data.finish_by_hour, int):
            self.finish_by_hour = int(car_data.finish_by_hour)
        else:
            self.finish_by_hour = math.ceil(float(self.ADapi.get_state(car_data.finish_by_hour,
                namespace = self.namespace))
            )
            self.ADapi.listen_state(self._finishByHourListen, car_data.finish_by_hour,
                namespace = self.namespace
            )

        # Switch to start charging now
        if isinstance(car_data.charge_now, str):
            self.charge_now_HA_switch:str = car_data.charge_now
            self.charge_now = self.ADapi.get_state(car_data.charge_now, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._chargeNowListen, car_data.charge_now,
                namespace = self.namespace
            )
        else:
            self.charge_now:bool = car_data.charge_now

        # Switch to charge only on solar
        if isinstance(car_data.charge_only_on_solar, str):
            self.charge_only_on_solar = self.ADapi.get_state(car_data.charge_only_on_solar, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._charge_only_on_solar_Listen, car_data.charge_only_on_solar,
                namespace = self.namespace
            )
        else:
            self.charge_only_on_solar:bool = car_data.charge_only_on_solar

        # Helper Variables:
        self.charging_on_solar:bool = False
        self.pct_start_charge:float = 100

        # Charger objects:
        self.connected_charger: Optional[Charger] = None
        self.onboard_charger: Optional[Charger] = None
        Registry.register_car(self)

        if self.car_data.charge_limit is not None:
            self.car_data.kWh_remain_to_charge:float = self.kWhRemaining()
            self.ADapi.listen_state(self.ChargeLimitChanged, self.car_data.charge_limit,
                namespace = self.namespace
            )
        else:
            self.car_data.kWh_remain_to_charge:float = -2

        # Set up listeners
        if self.car_data.charger_sensor is not None:
            #self.ADapi.listen_state(self.car_Car_ChargeCableConnected, self.car_data.charger_sensor,
            #    namespace = self.namespace,
            #    new = 'on'
            #)
            self.ADapi.listen_state(self.car_ChargeCableDisconnected, self.car_data.charger_sensor,
                namespace = self.namespace,
                new = 'off',
                duration = 700
            )

        self.find_Chargetime_Whenhome_handler = None

        if self.car_data.location_tracker is None:
            self.ADapi.log(
                f"Car not configured up with 'location_tracker'. Please update your config, or you might experience charging stopping when not home",
                level = 'WARNING'
                )

        """ TODO Departure / Maxrange handling: To be re-written before implementation
            Set a departure time in a HA datetime sensor for when car will be finished charging to 100%,
            to have a optimal battery when departing.
        """
        self.max_range_handler = None
        self.start_charging_max = None

        """ Add Maxrange solution for charging finished to 100% at given time.
            #self.ADapi.listen_state(self.MaxRangeListener, self.departure, namespace = self.namespace, duration = 5 )
        """

        """ End initialization Car Class
        """

    def set_connected_charger(self, charger: Charger) -> None:
        Registry.set_link(self, charger)

        # Functions on when to charge Car
    def _finishByHourListen(self, entity, attribute, old, new, kwargs) -> None:
        self.finish_by_hour = math.ceil(float(new))
        if self.kWhRemaining() > 0:
            self.findNewChargeTime()

    def _chargeNowListen(self, entity, attribute, old, new, kwargs) -> None:
        self.charge_now = new == 'on'
        if (
            new == 'on'
            and old == 'off'
            and self.connected_charger is not None
        ):
            self.startCharging()
        elif (
            new == 'off'
            and old == 'on'
            and self.kWhRemaining() > 0
        ):
            self.findNewChargeTime()

    def turnOff_Charge_now(self) -> None:
        """ Turns smart charging on again. """
        if isinstance(self.charge_now, str):
            self.ADapi.call_service('input_boolean/turn_off',
                entity_id = self.charge_now_HA_switch,
                namespace = self.namespace,
            )
        self.charge_now = False

    def _charge_only_on_solar_Listen(self, entity, attribute, old, new, kwargs) -> None:
        self.charge_only_on_solar = new == 'on'
        if new == 'on':
            self._handleChargeCompletion()
        elif new == 'off':
            if self.kWhRemaining() > 0:
                self.findNewChargeTime()

        # Functions for charge times
    def findNewChargeTimeAt(self, kwargs) -> None:
        """ Function to run when initialized and when new prices arrive. """
        self.findNewChargeTime()

    def findNewChargeTime(self) -> None:
        """ Find new chargetime for car. """
        if not self.isConnected():
            stack = inspect.stack()
            self.ADapi.log(f"Find New Chargetime called for {self.carName} from {stack[1].function} when car is not connected.") ###
        now = self.ADapi.datetime(aware=True)
        if self.dontStopMeNow():
            return
        if self.charging_scheduled_with_updated_data():
            return
        startcharge = False
        charger_state = self.getCarChargerState()
        if self.connected_charger is None:
            if charger_state != 'NoPower':
                Registry.set_link(self, self.onboard_charger)
            else:
                return
        if (
            charger_state not in ('Disconnected', 'Complete') or
            self.connected_charger.getChargingState() not in ('Disconnected', 'Complete')
        ):
            if (
                not self.charging_on_solar
                and not self.charge_only_on_solar
            ):
                cancel_timer_handler(ADapi = self.ADapi, handler = self.charging_scheduler.informHandler, name = self.carName)
                startcharge = self.charging_scheduler.queueForCharging(
                    vehicle_id = self.vehicle_id,
                    kWhRemaining = self.car_data.kWh_remain_to_charge,
                    maxAmps = self.getCarMaxAmps(),
                    voltPhase = self.connected_charger.charger_data.voltPhase,
                    finish_by_hour = self.finish_by_hour,
                    priority = self.car_data.priority,
                    name = self.carName
                )
                self.charging_scheduler.informHandler = self.ADapi.run_in(self.charging_scheduler.notifyChargeTime, 3)

                if (
                    charger_state == 'Charging'
                    and not startcharge
                ):
                    start, stop = self.charging_scheduler.getChargingTime(vehicle_id = self.vehicle_id)
                    match start:
                        case None:
                            if not self.charging_scheduler.isChargingTime(vehicle_id = self.vehicle_id):
                                self.stopCharging()
                        case _ if start - timedelta(minutes=12) > now:
                            self.stopCharging()
                elif (
                    charger_state in ('NoPower', 'Stopped')
                    and startcharge
                ):
                    self.startCharging()

    def removeFromQueue(self) -> None:
        """ Removes car from chargequeue
        """
        self.charging_scheduler.removeFromQueue(vehicle_id = self.vehicle_id)

    def _handleChargeCompletion(self):
        self.turnOff_Charge_now()
        self.removeFromQueue()
        self.car_data.kWh_remain_to_charge = -1

    def charging_scheduled_with_updated_data(self) -> bool:
        """ returns if car has charging scheduled
        """
        return self.charging_scheduler.charging_scheduled_with_updated_data(vehicle_id = self.vehicle_id,
                                                     kWhRemaining = self.car_data.kWh_remain_to_charge,
                                                     finish_by_hour = self.finish_by_hour)

        # Functions to react to car sensors
    def car_Car_ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge cable connected for car.
        """
        pass

    def car_ChargeCableDisconnected(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge cable disconnected for car.
        """
        if self.connected_charger is not None:
            if self.connected_charger.getChargingState() == 'Disconnected':
                if self.connected_charger.connected_vehicle.onboard_charger is self.connected_charger:
                    self.connected_charger._CleanUpWhenChargingStopped()
                else:
                    Registry.unlink(self)
                    Registry.set_link(self, self.onboard_charger)

            if self.max_range_handler is not None:
                # TODO: Program charging to max at departure time.
                # @HERE: Call a function that will cancel handler when car is disconnected
                #self.ADapi.run_in(self.resetMaxRangeCharging, 1)
                self.ADapi.log(f"{self.charger} Has a max_range_handler. Not Programmed yet", level = 'DEBUG')

    def isConnected(self) -> bool:
        """ Returns True if charge cable is connected.
        """
        if self.getLocation() == 'home':
            if self.car_data.charger_sensor is not None:
                return self.ADapi.get_state(self.car_data.charger_sensor, namespace = self.namespace) == 'on'
            if self.connected_charger is not None:
                return self.connected_charger.getChargingState() not in ['Disconnected']
            return True
        return False

    def asleep(self) -> bool:
        """ Returns True if car is sleeping.
        """
        if self.car_data.asleep_sensor and self._polling_of_data():
            return self.ADapi.get_state(self.car_data.asleep_sensor, namespace = self.namespace) == 'on'
        return False

    def wakeMeUp(self) -> None:
        """ Function to wake up connected cars.
        """
        pass

    def isOnline(self) -> bool:
        """ Returns True if car in online.
        """
        if self.car_data.online_sensor:
            return self.ADapi.get_state(self.car_data.online_sensor, namespace = self.namespace) == 'on'
        return True

    def getLocation(self) -> str:
        """ Returns location of the vehicle based on sones from Home Assistant.
        """
        if self.car_data.location_tracker:
            return self.ADapi.get_state(self.car_data.location_tracker, namespace = self.namespace)
        return 'home'

    def SoftwareUpdates(self) -> bool:
        """ Return True if car is updating software.
        """
        return False

    def forceAPIupdate(self) -> None:
        """ Function to force a new API pull on the vehicle.
        """
        pass

    def _polling_of_data(self) -> bool:
        """ Polling of data is a switch that disables communication with the car when switched off.
            TODO: Implement checks to not control/wake car if this is off.
        """
        if self.car_data.polling_switch:
            return self.ADapi.get_state(self.car_data.polling_switch, namespace = self.namespace) == 'on'
        return True

    def recentlyUpdated(self) -> bool:
        """ Returns True if car data is updated within the last 12 minutes.
        """
        if self.car_data.data_last_update_time:
            last_update = self.ADapi.convert_utc(self.ADapi.get_state(self.car_data.data_last_update_time,
                namespace = self.namespace)
            )
            now = self.ADapi.datetime(aware=True)
            stale_time = now - last_update
            if stale_time < timedelta(minutes = 12):
                return False
        return True

    def dontStopMeNow(self) -> bool:
        """ Returns true if charger should not or can not be stopped.
        """
        if (
            self.charge_now
            or self.charging_on_solar
            or self.SoftwareUpdates()
        ):
            return True
        return False

    def kWhRemaining(self) -> float:
        """ Calculates kWh remaining to charge car from battery sensor/size and charge limit.
        """
        if self.car_data.charge_limit:
            battery_pct = self.car_battery_soc()
            limit_pct = self.ADapi.get_state(self.car_data.charge_limit, namespace = self.namespace)
            try:
                battery_pct = float(battery_pct)
                limit_pct = float(limit_pct)
            except (ValueError, TypeError) as ve:
                try:
                    kWhRemain = float(self.car_data.kWh_remain_to_charge)
                except Exception:
                    kWhRemain = -1
                    self.car_data.kWh_remain_to_charge = -1
                    if self.getLocation() in ('home', 'unknown'):
                        self.wakeMeUp() # Wake up car to get proper value.
                else:
                    self.ADapi.log(
                        f"Not able to calculate kWh Remaining To Charge based on battery soc: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                        f"Return existing value: {self.car_data.kWh_remain_to_charge}. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                return kWhRemain
            except Exception as e:
                self.ADapi.log(
                    f"Not able to calculate kWh Remaining To Charge based on battery soc: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                    f"Return existing value: {self.car_data.kWh_remain_to_charge}. Exception: {e}",
                    level = 'WARNING'
                )
                return self.car_data.kWh_remain_to_charge

            if battery_pct < limit_pct:
                percentRemainToCharge = limit_pct - battery_pct
                self.car_data.kWh_remain_to_charge = (percentRemainToCharge / 100) * self.car_data.battery_size
            else:
                self.car_data.kWh_remain_to_charge = -1
            return self.car_data.kWh_remain_to_charge
        return -2

    def car_battery_soc(self) -> int:
        """ Returns battery State of charge.
        """
        SOC = -1
        if self.car_data.battery_sensor:
            try:
                SOC = float(self.ADapi.get_state(self.car_data.battery_sensor, namespace = self.namespace))
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} Not able to get SOC. Trying alternative calculations. ValueError: {ve}",
                    level = 'DEBUG'
                )
            except Exception as e:
                self.ADapi.log(
                    f"{self.carName} Not able to get SOC. Trying alternative calculations. Exception: {e}",
                    level = 'WARNING'
                )
        if SOC == -1:
            try:
                kWhRemain = float(self.car_data.kWh_remain_to_charge)
            except Exception:
                kWhRemain = -1
            if kWhRemain == -1:
                SOC = 100
            else: # TODO: Find a way to calculate
                SOC = 10
        return SOC

    def changeChargeLimit(self, chargeLimit:int = 100 ) -> None:
        """ Change charge limit.
        """
        self.car_data.old_charge_limit = self.ADapi.get_state(self.car_data.charge_limit, namespace = self.namespace)
        self.ADapi.call_service('number/set_value',
            value = chargeLimit,
            entity_id = self.car_data.charge_limit,
            namespace = self.namespace
        )

    def ChargeLimitChanged(self, entity, attribute, old, new, kwargs) -> None:
        """ Charge limit changed.
        """
        if self.connected_charger is not None:
            try:
                self.car_data.current_charge_limit = int(new)
                self.car_data.old_charge_limit = int(old)
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} new charge limit: {new}. Error: {ve}",
                    level = 'DEBUG'
                )
                return
            try:
                battery_state = float(self.ADapi.get_state(self.car_data.battery_sensor,
                    namespace = self.namespace)
                )
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.carName} battery state error {battery_state} when setting new charge limit: {new}. Error: {ve}",
                    level = 'DEBUG'
                )
                return
            if battery_state > float(new):
                self.connected_charger._CleanUpWhenChargingStopped()

            elif self.kWhRemaining() > 0:
                self.findNewChargeTime()

    def isChargingAtMaxAmps(self) -> bool:
        """ Returns True if the charging speed is at maximum.
        """
        if self.car_data.car_limit_max_ampere is None:
            return self.connected_charger.getmaxChargingAmps() <= self.connected_charger.charger_data.ampereCharging
        return self.car_data.car_limit_max_ampere <= self.connected_charger.charger_data.ampereCharging

    def getCarMaxAmps(self) -> int:
        if self.car_data.car_limit_max_ampere is None:
            return self.connected_charger.getmaxChargingAmps()
        return self.car_data.car_limit_max_ampere

    def getCarChargerState(self) -> str:
        """ Returns the charging state of the car.
            Valid returns: 'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting' / 'NoPower'.
        """
        if self.car_data.charger_sensor is not None:
            try:
                state = self.ADapi.get_state(self.car_data.charger_sensor,
                    namespace = self.namespace,
                    attribute = 'charging_state'
                )
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.charger} Could not get attribute = 'charging_state' from: "
                    f"{self.ADapi.get_state(self.car_data.charger_sensor, namespace = self.namespace)} "
                    f"Error: {ve}",
                    level = 'DEBUG'
                )
            else:
                if state == 'Starting':
                    state = 'Charging'
                return state
        
        if self.connected_charger is not None:
            self.ADapi.log(f"Returning connected charger state {self.connected_charger.getChargingState()} for {self.carName} in getCarChargerState") ###
            return self.connected_charger.getChargingState()
        return None

    def startCharging(self) -> None:
        """ Starts controlling charger.
        """
        if (
            self.getCarChargerState() == 'Stopped'
            or self.connected_charger.getChargingState() in {'awaiting_start', 'Stopped'}
        ):
            self.connected_charger.startCharging()
        elif self.getCarChargerState() == 'Complete':
            self.connected_charger._CleanUpWhenChargingStopped()

    def stopCharging(self, force_stop:bool = False) -> None:
        """ Stops controlling charger.
        """
        if self.connected_charger.getChargingState() in ('Charging', 'Starting'):
            self.connected_charger.stopCharging(force_stop = force_stop)


class Tesla_car(Car):

    def __init__(self, api,
        namespace,
        carName,
        car_data,
        charging_scheduler,
    ):

        self.vehicle_id = api.get_state(car_data.online_sensor,
            namespace = namespace,
            attribute = 'id'
        )

        super().__init__(
            api = api,
            namespace = namespace,
            carName = carName,
            vehicle_id = self.vehicle_id,
            car_data = car_data,
            charging_scheduler = charging_scheduler,
        )
        self.onboard_charger = None

        if self.car_data.destination_location_tracker:
           self.ADapi.listen_state(self.destination_updated, self.car_data.destination_location_tracker,
            namespace = self.namespace
        )

        """ End initialization Tesla Car Class
        """

    def wakeMeUp(self) -> None:
        """ Function to wake up connected cars.
        """
        if self._polling_of_data():
            if self.ADapi.get_state(self.car_data.charger_sensor, namespace = self.namespace) not in ('Complete', 'Disconnected'):
                if (
                    not self.recentlyUpdated()
                    and self.asleep()
                ):
                    self.ADapi.call_service('tesla_custom/api',
                        namespace = self.namespace,
                        command = 'WAKE_UP',
                        parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep' : True}
                    )
                self.forceAPIupdate()

    def SoftwareUpdates(self) -> bool:
        """ Return True if car is updating software.
        """
        if self.ADapi.get_state(self.car_data.software_update, namespace = self.namespace) not in UNAVAIL:
            if self.ADapi.get_state(self.car_data.software_update, namespace = self.namespace, attribute = 'in_progress') != False:
                return True
        return False

    def forceAPIupdate(self) -> None:
        """ Function to force a new API pull on the vehicle.
        """
        if self._polling_of_data():
            self.ADapi.create_task(self._force_API_update())

    async def _force_API_update(self):
        await self.ADapi.call_service('button/press',
            namespace = self.namespace,
            entity_id = self.car_data.force_data_update
        )

    def destination_updated(self, entity, attribute, old, new, kwargs) -> None:
        """ Get arrival time if destination == 'home'
            and use estimated battery on arrival to calculate chargetime.
        """
        ### TODO: Actual calculation based on arrival time
        if new == 'home':
            energy_at_arrival= self.ADapi.get_state(self.car_data.arrival_time,
                namespace = self.namespace,
                attribute='Energy at arrival'
            )
            if energy_at_arrival > 0:
                self.car_data.kWh_remain_to_charge = self.car_data.pref_charge_limit - energy_at_arrival
                self.ADapi.log(
                    f"Arrival UTC: {self.ADapi.convert_utc(self.ADapi.get_state(self.car_data.arrival_time, namespace = self.namespace))} "
                    f"Timedelta: {self.ADapi.convert_utc(self.ADapi.get_state(self.car_data.arrival_time, namespace = self.namespace)) - self.ADapi.datetime(aware=True)} "
                    f"Energy at Arrival: {energy_at_arrival}. To charge: {self.car_data.kWh_remain_to_charge}"
                )###
