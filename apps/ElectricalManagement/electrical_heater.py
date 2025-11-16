from __future__ import annotations

import math

from datetime import timedelta

from typing import Any, Dict, Tuple

from pydantic_models import TempConsumption
from utils import (
    cancel_timer_handler,
    cancel_listen_handler,
    floor_even
)

from scheduler import Scheduler

UNAVAIL = ('unavailable', 'unknown')

class Heater:
    """ Heater
        Parent class for on_off_switch and electrical heaters
        Sets up times to save/spend based on electricity price
    """
    def __init__(self,
        api,
        namespace,
        heater,
        heater_data,
        electricalPriceApp,
        charging_scheduler,
        notify_app,
        print_save_hours
    ):
        self.ADapi = api
        self.namespace = namespace
        self.heater = heater
        self.heater_data = heater_data
        self.electricalPriceApp = electricalPriceApp
        self.charging_scheduler = charging_scheduler
        self.notify_app = notify_app
        self.print_save_hours = print_save_hours

        # Vacation setup
        if self.heater_data.vacation is not None and self.ADapi.entity_exists(self.heater_data.vacation, namespace = self.namespace):
            self.away_state = self.ADapi.get_state(self.heater_data.vacation, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self._awayStateListen_Heater, self.heater_data.vacation,
                namespace = self.namespace
            )
        else:
            self.away_state = False

        # Automate setup
        if isinstance(self.heater_data.automate, str):
            self.automate = self.ADapi.get_state(self.heater_data.automate, namespace = self.namespace)  == 'on'
            self.ADapi.listen_state(self.automateStateListen, self.heater_data.automate,
                namespace = self.namespace
            )
        elif isinstance(self.heater_data.automate, bool):
            self.automate = self.heater_data.automate

        # Consumption data
        self.reset_continuous_hours:bool = False
        self.time_to_spend:list = []
        self.kWh_consumption_when_turned_on:float = 0.0
        self.isSaveState:bool = False
        self.isOverconsumption:bool = False
        self.increase_now:bool = False
        self.last_reduced_state = self.ADapi.datetime(aware = True) - timedelta(minutes=20)

        # Handlers
        self._consumption_stops_register_usage_handler = None
        self.checkConsumption_handler = None

        # Helpers used on vacation
        self.HeatAt = None
        self.EndAt = None
        self.price:float = 0

        # Weather sensors
        self.out_temp:float = 10
        self.rain_amount:float = 0
        self.wind_amount:float = 0
        self.ADapi.listen_event(self.weather_event, 'WEATHER_CHANGE', namespace=self.namespace)

        # Finding data if not set to persistent
        if self.heater_data.normal_power < 30:
            self.ADapi.listen_state(self._set_normal_power, self.heater_data.consumptionSensor,
                constrain_state=lambda x: float(x) > 30,
                oneshot = True,
                namespace = self.namespace
            )
    
    def _set_normal_power(self, entity, attribute, old, new, kwargs) -> None:
        if float(new) < 30:
            self.heater_data.normal_power = float(new)

    def _awayStateListen_Heater(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes in vacation switch and requests heater to set new state
        """
        self.away_state = new == 'on'
        self.heater_setNewValues()

    def automateStateListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes to automate switch and requests heater to set new state if automation is turned back on
        """
        self.automate = new == 'on'
        self.heater_setNewValues()

    def heater_getNewPrices(self, kwargs) -> None:
        """ Updates time to save and spend based on self.electricalPriceApp.find_times_to_save()
            Will also find cheapest times to heat hotwater boilers and other on/off switches when on vacation.
        """
        now = self.ADapi.datetime(aware = True)
        self.heater_data.time_to_save = self.electricalPriceApp.find_times_to_save(
            pricedrop = self.heater_data.pricedrop,
            max_continuous_hours = self.heater_data.max_continuous_hours,
            on_for_minimum = self.heater_data.on_for_minimum,
            pricedifference_increase = self.heater_data.pricedifference_increase,
            reset_continuous_hours = self.reset_continuous_hours,
            previous_save_hours = self.heater_data.time_to_save
        )
        if (
            self.away_state
            and (self.HeatAt is None
            or self.electricalPriceApp.tomorrow_valid)
        ):
            self.HeatAt, self.EndAt, self.price = self.electricalPriceApp.get_Continuous_Cheapest_Time(
                hoursTotal = 2,
                calculateBeforeNextDayPrices = not self.electricalPriceApp.tomorrow_valid,
		        finishByHour = 24,
                startBeforePrice = 0.02, 
                stopAtPriceIncrease = 0.01
            )

        elif not self.away_state:
            self.HeatAt = None
            self.EndAt = None

        self.heater_setNewValues()

        if self.print_save_hours and self.heater_data.time_to_save:
            self.ADapi.log(f"{self.heater} save hours:{self.electricalPriceApp.print_peaks(self.heater_data.time_to_save)}")

    def heater_setNewValues(self, kwargs=None) -> None:
        """ Turns heater on or off based on this hours electricity price.
        """
        isOn:bool = self.ADapi.get_state(self.heater, namespace = self.namespace) == 'on'
        now = self.ADapi.datetime(aware = True)

        if (
            self.isOverconsumption
            and isOn
        ):
            self.ADapi.call_service('switch/turn_off',
                entity_id = self.heater,
                namespace = self.namespace
            )
            self.isSaveState = True
            return
        if self.increase_now:
            if not isOn:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
            self.isSaveState = False
            return
        if (
            self._is_time_within_any_save_range()
            and self.automate
            and not self.charging_scheduler.isChargingTime()
        ):
            if isOn:
                self.ADapi.call_service('switch/turn_off',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
            self.isSaveState = True
            return
        elif not isOn:
            if (
                self.HeatAt is not None
                and self.away_state
            ):
                if (
                    (start := self.HeatAt) <= now < (end := self.EndAt)
                    or self.electricalPriceApp.electricity_price_now() <= self.price + (self.heater_data.pricedrop/2)
                ):
                    self.ADapi.call_service('switch/turn_on',
                        entity_id = self.heater,
                        namespace = self.namespace
                    )
                self.isSaveState = False
                return
            else:
                self.ADapi.call_service('switch/turn_on',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
                self.isSaveState = False
                return
        elif(
            isOn
            and self.HeatAt is not None
            and self.away_state
        ):
            if (
                (start := self.HeatAt) <= now < (end := self.EndAt)
                or self.electricalPriceApp.electricity_price_now() <= self.price + (self.heater_data.pricedrop/2)
            ):
                return
            if self.heater_data.validConsumptionSensor:
                if float(self.ADapi.get_state(self.heater_data.consumptionSensor, namespace = self.namespace)) > 20:
                    self.ADapi.listen_state(self.turnOffHeaterAfterConsumption, self.heater_data.consumptionSensor,
                        namespace = self.namespace,
                        constrain_state=lambda x: float(x) < 20
                    )
                    return
            self.ADapi.call_service('switch/turn_off',
                entity_id = self.heater,
                namespace = self.namespace
            )

    def turnOffHeaterAfterConsumption(self, entity, attribute, old, new, kwargs) -> None:
        """ Turns off heater after consumption is below 20W
        """
        self.ADapi.call_service('switch/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )

    def turn_on_heater(self) -> None:
        """ Turns heater back to normal operation after fire.
        """
        self.heater_setNewValues()

    def turn_off_heater(self) -> None:
        """ Turns heater off.
        """
        self.ADapi.call_service('switch/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )

        # Functions called from electrical
    def setPreviousState(self) -> None:
        """ Set heater to previous state after overconsumption.
        """
        self.isOverconsumption = False
        self.heater_setNewValues()

    def removeSaveState(self) -> None:
        """ Set heater to normal state.
        """
        self.isSaveState = False
        self.isOverconsumption = False
        self.heater_setNewValues()

    def setSaveState(self) -> None:
        """ Set heater to save state when overconsumption.
        """
        self.isOverconsumption = True
        self.heater_setNewValues()

    def setIncreaseState(self) -> None:
        """ Set heater to increase temperature when electricity production is higher that consumption.
        """
        self.increase_now = True
        self.isSaveState = False
        self.heater_setNewValues()

    def get_heater_consumption(self) -> Tuple[float, bool]:
        if self.heater_data.validConsumptionSensor:
            consumption_now = self.ADapi.get_state(self.heater_data.consumptionSensor, namespace = self.namespace)
            if consumption_now not in UNAVAIL:
                try:
                    return float(consumption_now), True
                except (TypeError, ValueError):
                    pass
        return self.heater_data.normal_power, False

    def get_heater_kWh_consumption(self) -> float:
        try:
            consumption = float(self.ADapi.get_state(self.heater_data.kWhconsumptionSensor, namespace = self.namespace))
        except (TypeError, AttributeError) as ve:
            self.ADapi.log(
                f"Could not get kWh consumption for {self.heater} {consumption} Error: {ve}",
                level = 'DEBUG'
            )
            return None
        else:
            return consumption

        # Functions to calculate and log consumption to persistent storage
    def findConsumptionAfterTurnedOn(self, **kwargs) -> None:
        """ Starts to listen for how much heater consumes after it has been in save mode.
        """
        hoursOffInt = kwargs['hoursOffInt']
        if self.heater_data.kWhconsumptionSensor is None or not self.heater_data.validConsumptionSensor:
            return

        if (
            (self.ADapi.get_state(self.heater, namespace = self.namespace) != 'off' or
            self.isOverconsumption)
            and not self.away_state
            and self.automate
        ):
            kWh_consumption = self.get_heater_kWh_consumption()
            if kWh_consumption is None:
                return
            self.kWh_consumption_when_turned_on = kWh_consumption

            self._consumption_stops_register_usage_handler = self.ADapi.listen_state(self._consumption_stops_register_usage, self.heater_data.consumptionSensor,
                namespace = self.namespace,
                constrain_state=lambda x: float(x) < 20,
                hoursOffInt = hoursOffInt,
                oneshot = True
            )
            cancel_timer_handler(ADapi = self.ADapi, handler = self.checkConsumption_handler, name = self.heater)
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 1200, hoursOffInt = hoursOffInt)

    def checkIfConsumption(self, kwargs) -> None:
        """ Checks if there is consumption after 'findConsumptionAfterTurnedOn' starts listening.
            If there is no consumption it will cancel the timer.
        """
        if not self.heater_data.validConsumptionSensor:
            self.ADapi.log(f"Consumption sensor for {self.heater} not Valid. Should not see this anymore...")
            if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkConsumption_handler, name = self.heater):
               self.checkConsumption_handler = None
            return

        hoursOffInt = kwargs['hoursOffInt']

        if self.isOverconsumption:
            cancel_timer_handler(ADapi = self.ADapi, handler = self.checkConsumption_handler, name = self.heater)
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 600, hoursOffInt = hoursOffInt)
            return

        wattconsumption, valid_consumption = self.get_heater_consumption()
        if valid_consumption:
            if wattconsumption < 30:
                if cancel_listen_handler(ADapi = self.ADapi, handler = self._consumption_stops_register_usage_handler, name = self.heater):
                    self._consumption_stops_register_usage_handler = None
                    self.registerConsumption(hoursOffInt = hoursOffInt)
            elif self._consumption_stops_register_usage_handler is None:
                self._consumption_stops_register_usage_handler = self.ADapi.listen_state(self._consumption_stops_register_usage, self.heater_data.consumptionSensor,
                    namespace = self.namespace,
                    constrain_state=lambda x: float(x) < 20,
                    hoursOffInt = hoursOffInt,
                    oneshot = True
                )

    def _consumption_stops_register_usage(self, entity, attribute, old, new, **kwargs) -> None:
        """ Registers consumption to persistent storage after heater has been off.
        """
        hoursOffInt = kwargs['hoursOffInt']
        if cancel_timer_handler(ADapi = self.ADapi, handler = self.checkConsumption_handler, name = self.heater):
            self.checkConsumption_handler = None

        if self.isOverconsumption:
            self.checkConsumption_handler = self.ADapi.run_in(self.checkIfConsumption, 600, hoursOffInt = hoursOffInt)
            return

        if cancel_listen_handler(ADapi = self.ADapi, handler = self._consumption_stops_register_usage_handler, name = self.heater):
           self._consumption_stops_register_usage_handler = None

        try:
            if self.heater_data.normal_power < float(old):
                self.heater_data.normal_power = float(old)
        except (TypeError, ValueError):
            pass
        self.registerConsumption(hoursOffInt = hoursOffInt)

    def registerConsumption(self, hoursOffInt:int = 0) -> None:
        consumption:float = 0
        try:
            consumption = float(self.ADapi.get_state(self.heater_data.kWhconsumptionSensor, namespace = self.namespace))
            consumption -= self.kWh_consumption_when_turned_on
        except (TypeError, AttributeError) as ve:
            self.ADapi.log(
                f"Could not get consumption for {self.heater} to register data. {consumption} Error: {ve}",
                level = 'DEBUG'
            )
            return
        if consumption == 0:
            consumption = 0.01 # Avoid multiplications by 0.
        if consumption < 0:
            return

        if self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off':
            return

        out_temp_even = floor_even(self.out_temp)

        if hoursOffInt not in self.heater_data.ConsumptionData:
            self.heater_data.ConsumptionData[hoursOffInt] = {}

        inner_dict: Dict[int, TempConsumption] = self.heater_data.ConsumptionData[
            hoursOffInt
        ]

        if out_temp_even not in inner_dict:
            inner_dict[out_temp_even] = TempConsumption(
                Consumption=round(consumption, 2),
                Counter=1,
            )
        else:
            existing: TempConsumption = inner_dict[out_temp_even]
            counter = (existing.Counter or 0) + 1
            avg_consumption = round(
                (
                    (existing.Consumption or 0) * (existing.Counter or 0)
                    + consumption
                )
                / counter,
                2,
            )
            if counter > 100:
                counter = 10
            existing.Consumption = avg_consumption
            existing.Counter = counter


        # Helper functions for windows
    def windowOpened(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to windows opened.
        """
        if self.numWindowsOpened() != 0:
            self.windows_is_open = True
            self.notify_on_window_closed = True
            self.heater_setNewValues()

    def windowClosed(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to windows closed and checks if other windows are opened.
        """
        if self.numWindowsOpened() == 0:
            self.windows_is_open = False
            self.notify_on_window_open = True
            self.heater_setNewValues()

    def numWindowsOpened(self) -> int:
        """ Returns number of windows opened.
        """
        opened = 0
        for window in self.heater_data.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened

    def _is_time_within_any_save_range(self):
        now = self.ADapi.datetime(aware = True)
        for range_item in self.heater_data.time_to_save:
            if (start := range_item.start) <= now < (end := range_item.end):
                return True
        return False

    def _is_time_within_any_spend_range(self):
        now = self.ADapi.datetime(aware = True)
        for range_item in self.time_to_spend:
            if (start := range_item.start) <= now < (end := range_item.end):
                return True
        return False

    def weather_event(self, event_name, data, **kwargs) -> None:
        """ Listens for weather change from the weather app """

        self.out_temp = float(data['temp'])
        self.rain_amount = float(data['rain'])
        self.wind_amount = float(data['wind'])

class Climate(Heater):
    """ Child class of Heater
        For controlling electrical heaters to heat off peak hours.
    """
    def __init__(self,
        api,
        namespace,
        heater,
        heater_data,
        electricalPriceApp,
        charging_scheduler,
        notify_app,
        print_save_hours
    ):

        # Sensors
        if heater_data.target_indoor_input is not None:
            api.listen_state(self.updateTarget, heater_data.target_indoor_input,
                namespace = namespace
            )
            self.target_indoor_temp = float(api.get_state(heater_data.target_indoor_input, namespace = namespace))
        else:
            self.target_indoor_temp:float = heater_data.target_indoor_temp

        super().__init__(
            api = api,
            namespace = namespace,
            heater = heater,
            heater_data = heater_data,
            electricalPriceApp = electricalPriceApp,
            charging_scheduler = charging_scheduler,
            notify_app = notify_app,
            print_save_hours = print_save_hours,
        )
        self.reset_continuous_hours = True

        self.windows_is_open:bool = False
        self.notify_on_window_open:bool = True
        self.notify_on_window_closed:bool = False
        for window in self.heater_data.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                self.windows_is_open = True

            self.ADapi.listen_state(self.windowOpened, window,
                new = 'on',
                duration = 120,
                namespace = self.namespace
            )
            self.ADapi.listen_state(self.windowClosed, window,
                new = 'off',
                namespace = self.namespace
            )

        try:
            self.min_temp = self.ADapi.get_state(self.heater,
                namespace = self.namespace,
                attribute = 'min_temp'
            )
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.heater} Attribute = 'min_temp' is not found in: "
                f"{self.ADapi.get_state(self.heater, namespace = self.namespace, attribute = 'all')} "
                f"ValueError: {ve}",
                level = 'DEBUG'
            )
            self.min_temp = 5

        # Get new prices to save and in addition to turn up heat for heaters before expensive hours
    def heater_getNewPrices(self, kwargs) -> None:
        """ Updates time to save and spend based on self.electricalPriceApp.find_times_to_spend()
        """
        super().heater_getNewPrices(0)
        self.time_to_spend = self.electricalPriceApp.find_times_to_spend(
            priceincrease = self.heater_data.priceincrease
        )

        if self.time_to_spend and self.print_save_hours:
            self.ADapi.log(f"{self.heater} Extra heating at: {self.electricalPriceApp.print_peaks(self.time_to_spend)}", level = 'INFO')

    def _awayStateListen_Heater(self, entity, attribute, old, new, kwargs) -> None:
        """ Listen for changes in vacation switch and requests heater to set new state
        """
        self.away_state = new == 'on'
        if (
            self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off'
            and new == 'off'
        ):
            try:
                self.ADapi.call_service('climate/set_hvac_mode',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    hvac_mode = 'heat'
                )
            except Exception as e:
                self.ADapi.log(f"Not able to set hvac_mode to heat for {self.heater}. Exception: {e}", level = 'INFO')
        self.heater_setNewValues()

    def find_target_temperatures(self) -> int:
        """ Helper function to find correct dictionary element in temperatures
        """
        target_num = 0
        for target_num, target_temp in enumerate(self.heater_data.temperatures):
            if target_temp['out'] >= self.out_temp:
                if target_num != 0:
                    target_num -= 1
                return target_num

        return target_num

    def turn_on_heater(self) -> None:
        """ Turns climate on after fire alarm.
        """
        self.ADapi.call_service('climate/turn_on',
            entity_id = self.heater,
            namespace = self.namespace
        )
        self.heater_setNewValues()

    def turn_off_heater(self) -> None:
        """ Turns climate off.
        """
        self.ADapi.call_service('climate/turn_off',
            entity_id = self.heater,
            namespace = self.namespace
        )

        # Functions to set temperature
    def setSaveState(self) -> None:
        """ Set heater to save state when overconsumption.
        """
        self.isOverconsumption = True
        if self.ADapi.get_state(self.heater, namespace = self.namespace) == 'heat':
            target_num = self.find_target_temperatures()
            target_temp = self.heater_data.temperatures[target_num]
            if self.heater_data.save_temp is not None:
                save_temp = self.heater_data.save_temp + target_temp['offset']
            elif 'save' in target_temp:
                save_temp = target_temp['save']
            else:
                save_temp = 10
            try:
                if float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='temperature')) > save_temp:
                    self.ADapi.call_service('climate/set_temperature',
                        namespace = self.namespace,
                        entity_id = self.heater,
                        temperature = save_temp
                    )
            except (TypeError, AttributeError) as ve:
                self.ADapi.call_service('climate/set_temperature',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    temperature = 10
                )
                self.ADapi.log(f"Error when trying to set temperature to {self.heater}: {ve}", level = 'DEBUG')

    def heater_setNewValues(self, kwargs=None) -> None:
        """ Adjusts temperature based on weather and time to save/spend
        """
        if (
            self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off'
            or self.isOverconsumption
        ):
            return
        self.isSaveState =  False
        target_num = self.find_target_temperatures()
        target_temp = self.heater_data.temperatures[target_num]

        try:
            heater_temp = float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='temperature'))
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"Error when trying to get currently set temperature to {self.heater}: {ve}",
                level = 'DEBUG'
            )
            heater_temp = self.target_indoor_temp
        except Exception as e:
            self.ADapi.log(
                f"Error when trying to get currently set temperature to {self.heater}. Exception: {e}",
                level = 'INFO'
            )
            heater_temp = self.target_indoor_temp

        in_temp:float = -50
        if self.heater_data.indoor_sensor_temp is not None:
            try:
                in_temp = float(self.ADapi.get_state(self.heater_data.indoor_sensor_temp, namespace = self.namespace))
            except (TypeError, AttributeError) as te:
                self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                self.ADapi.log(
                    f"Not able to get new inside temperature from {self.heater_data.indoor_sensor_temp}. Error: {e}",
                    level = 'DEBUG'
                )
        if in_temp == -50:
            try:
                in_temp = float(self.ADapi.get_state(self.heater, namespace = self.namespace, attribute='current_temperature'))
                self.ADapi.log(
                    f"{self.heater} Not able to get new inside temperature from {self.heater_data.indoor_sensor_temp}. "
                    f"Getting in temp from heater. It is: {in_temp}",
                    level = 'DEBUG'
                )
            except (TypeError, AttributeError) as te:
                self.ADapi.log(f"{self.heater} has no temperature. Probably offline. Error: {te}", level = 'DEBUG')
            except Exception as e:
                self.ADapi.log(f"Not able to get new inside temperature from {self.heater}. {e}", level = 'WARNING')

        # Set Target temperatures
        if 'offset' in target_temp:
            new_temperature = self.target_indoor_temp + target_temp['offset']
        elif 'normal' in target_temp:
            new_temperature = target_temp['normal']
        else:
            new_temperature = self.target_indoor_temp

        if self.heater_data.vacation_temp is not None:
            vacation_temp = self.heater_data.vacation_temp + target_temp['offset']
        elif 'away' in target_temp:
            vacation_temp = target_temp['away']
        else:
            vacation_temp = 5

        # Adjust temperature based on weather
        if self.rain_amount >= self.heater_data.rain_level:
            new_temperature += 1
        elif self.wind_amount >= self.heater_data.anemometer_speed:
            new_temperature += 1
        
        adjust = 0
        if self.heater_data.window_temp is not None:
            try:
                window_temp = float(self.ADapi.get_state(self.heater_data.window_temp, namespace = self.namespace))
            except (TypeError, AttributeError):
                window_temp = self.target_indoor_temp + self.heater_data.window_offset
                self.ADapi.log(f"{self.heater_data.window_temp} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                window_temp = self.target_indoor_temp + self.heater_data.window_offset
                self.ADapi.log(f"Not able to get temperature from {self.heater_data.window_temp}. {e}", level = 'DEBUG')
            if window_temp > self.target_indoor_temp + self.heater_data.window_offset:
                adjust = math.floor(float(window_temp - (self.target_indoor_temp + self.heater_data.window_offset)))

        if in_temp > self.target_indoor_temp:
            adjust += math.floor(float(in_temp - self.target_indoor_temp))
        
        new_temperature -= adjust

        if new_temperature < vacation_temp:
            new_temperature = vacation_temp

        # Windows
        if (
            not self.windows_is_open
            and self.notify_on_window_closed
            and in_temp >= self.target_indoor_temp + 10
            and self.out_temp > self.heater_data.getting_cold
        ):
            self.notify_app.send_notification(
                message = f"No Window near {self.heater} is open and it is getting hot inside! {in_temp}°",
                message_title = f"Window closed",
                message_recipient = self.heater_data.recipients,
                also_if_not_home = False
            )
            self.notify_on_window_closed = False
        
        if self.windows_is_open:
            new_temperature = vacation_temp
            if (
                self.notify_on_window_open
                and self.out_temp < self.heater_data.getting_cold
                and in_temp < self.heater_data.getting_cold
            ):
                self.notify_app.send_notification(
                    message = f"Window near {self.heater} is open and inside temperature is {in_temp}°",
                    message_title = "Window open",
                    message_recipient = self.heater_data.recipients,
                    also_if_not_home = False
                )
                self.notify_on_window_open = False

        # Holliday temperature
        elif self.away_state:
            new_temperature = vacation_temp

        # Peak and savings temperature
        if (
            self._is_time_within_any_save_range()
            and self.automate
        ):
            new_temperature = self.getSaveTemp(new_temperature, target_temp)
            self.isSaveState = True
        
        # Daytime Savings
        else:
            doDaytimeSaving = False
            for daytime in self.heater_data.daytime_savings:
                if (
                    'start' in daytime
                    and 'stop' in daytime
                ):
                    if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                        doDaytimeSaving = True
                        if 'presence' in daytime:
                            for presence in daytime['presence']:
                                if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                                    doDaytimeSaving = False

                elif 'presence' in daytime:
                    doDaytimeSaving = True
                    for presence in daytime['presence']:
                        if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                            doDaytimeSaving = False

            if doDaytimeSaving:
                new_temperature = self.getSaveTemp(new_temperature, target_temp)
                self.isSaveState = True

        # Low price for electricity or solar power
        if (
            self.increase_now
            or self._is_time_within_any_spend_range()
        ):
            new_temperature += 1

        # Avoid setting lower temp than climate minimum
        if new_temperature < self.min_temp:
            new_temperature = self.min_temp

        # Setting new temperature
        try:
            if heater_temp != new_temperature:
                self.ADapi.call_service('climate/set_temperature',
                    namespace = self.namespace,
                    entity_id = self.heater,
                    temperature = new_temperature
                )
        except (TypeError, AttributeError):
            self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')

    def getSaveTemp(self, new_temperature:float, target_temp:dict) -> float:
        """ Returns save temperature
        """
        if self.heater_data.save_temp_offset is not None:
            new_temperature += self.heater_data.save_temp_offset
        elif self.heater_data.save_temp is not None:
            if new_temperature > self.heater_data.save_temp + target_temp['offset']:
                new_temperature = self.heater_data.save_temp + target_temp['offset']
        elif 'save' in target_temp:
            if new_temperature > target_temp['save']:
                new_temperature = target_temp['save']
        else:
            new_temperature = 10

        return new_temperature

    def updateTarget(self, entity, attribute, old, new, kwargs):
        """ Reacts to target temperature for room beening updated.
        """
        self.target_indoor_temp = float(new)
        self.heater_setNewValues()

class On_off_switch(Heater):
    """ Child class of Heater
        Heating of on_off_switch off peak hours
        Turns on/off a switch depending og given input and electricity price
    """
    def __init__(self,
        api,
        heater,
        namespace,
        heater_data,
        electricalPriceApp,
        charging_scheduler,
        notify_app,
        print_save_hours
    ):

        super().__init__(
            api = api,
            namespace = namespace,
            heater = heater,
            heater_data = heater_data,
            electricalPriceApp = electricalPriceApp,
            charging_scheduler = charging_scheduler,
            notify_app = notify_app,
            print_save_hours = print_save_hours,
        )
