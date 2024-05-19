""" ElectricalManagement.

    @Pythm / https://github.com/Pythm

"""

__version__ = "0.1.3"

import appdaemon.plugins.hass.hassapi as hass
import datetime
import math
import json
import csv
import inspect

RECIPIENTS:list = []
JSON_PATH:str = ''
OUT_TEMP:float = 0.0
RAIN_AMOUNT:float = 0.0
WIND_AMOUNT:float = 0.0


class ElectricityPrice:

    def __init__(
        self,
        api,
        nordpool,
        daytax:float,
        nighttax:float,
        workday,
        power_support_above:float,
        support_amount:float
    ):

        self.ADapi = api
        self.nordpool_prices = None
        self.nordpool_last_updated = self.ADapi.datetime(aware=True)
        if nordpool:
            self.nordpool_prices = nordpool
        else:
            sensor_states = self.ADapi.get_state(entity='sensor')
            for sensor_id, sensor_states in sensor_states.items():
                if 'nordpool' in sensor_id:
                    self.nordpool_prices = sensor_id
                    break
        if not self.nordpool_prices:
            raise Exception(
                "Nordpool custom components not found. Please install Nordpool via HACS: https://github.com/custom-components/nordpool"
            )

        self.currency:str = self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'currency')
        self.daytax:float = daytax
        self.nighttax:float = nighttax
        self.workday = workday

        self.power_support_above:float = power_support_above
        self.support_amount:float = support_amount

        self.elpricestoday:list = []
        self.nordpool_todays_prices:list = []
        self.nordpool_tomorrow_prices:list = []
        self.sorted_elprices_today:list = []
        self.sorted_elprices_tomorrow:list = []

        self.getprices()
        self.ADapi.listen_state(self.update_price_rundaily, self.nordpool_prices,
            attribute = 'tomorrow'
        )


    def update_price_rundaily(self, entity, attribute, old, new, kwargs) -> None:
        self.getprices()


    def getprices(self) -> None:
        """ Fetches prices from Nordpool sensor and adds day and night tax
            
            TODO: Verify time with attributes from "Raw today" and "Raw tomorrow" containing datetime
            Fail every time summertime is starting/stopping due to one hour less/more.
        """
        self.elpricestoday = []
        isNotWorkday:bool = self.ADapi.get_state(self.workday) == 'off'

        # Todays prices
        try:
            self.nordpool_todays_prices = self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'today')
            for hour in range(0,len(self.nordpool_todays_prices)):
                calculated_support:float = 0.0 # Power support calculation
                if float(self.nordpool_todays_prices[hour]) > self.power_support_above:
                    calculated_support = (float(self.nordpool_todays_prices[hour]) - self.power_support_above ) * self.support_amount
                if (
                    hour < 6
                    or hour > 21
                    or datetime.datetime.today().weekday() > 4
                    or isNotWorkday
                ):
                    self.elpricestoday.insert(hour, round(float(self.nordpool_todays_prices[hour]) + self.nighttax - calculated_support, 3))
                else:
                    self.elpricestoday.insert(hour, round(float(self.nordpool_todays_prices[hour]) + self.daytax - calculated_support, 3))

        except Exception as e:
            self.ADapi.log(f"Nordpool prices today failed. Exception: {e}", level = 'DEBUG')
            self.ADapi.run_in(self.getprices, 1800)
            self.sorted_elprices_today = []
        else:
            self.sorted_elprices_today = sorted(self.elpricestoday)


        # Tomorrows prices if available
        if self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow_valid'):
            try:
                self.nordpool_tomorrow_prices = self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow')
                if (
                    len(self.nordpool_tomorrow_prices) > 0
                    and self.nordpool_todays_prices != self.nordpool_tomorrow_prices
                ):
                    for hour in range(0,len(self.nordpool_tomorrow_prices)):
                        calculated_support:float = 0.0 # Power support calculation
                        if float(self.nordpool_tomorrow_prices[hour]) > self.power_support_above:
                            calculated_support = (float(self.nordpool_tomorrow_prices[hour]) - self.power_support_above ) * self.support_amount

                        """
                            TODO: Does not check if tomorrow is holiday when applying day or night tax to tomorrows prices. 
                        """

                        if (
                            hour < 6
                            or hour > 21
                            or datetime.datetime.today().weekday() >= 4
                            or datetime.datetime.today().weekday() == 6
                        ):
                            self.elpricestoday.insert(hour+24, round(float(self.nordpool_tomorrow_prices[hour]) + self.nighttax - calculated_support, 3))
                        else:
                            self.elpricestoday.insert(hour+24, round(float(self.nordpool_tomorrow_prices[hour]) + self.daytax - calculated_support, 3))
                        self.sorted_elprices_tomorrow.insert(hour, self.elpricestoday[hour+24])

            except Exception as e:
                self.ADapi.log(f"Nordpool prices tomorrow failed. Occurs when changing to Summertime. Exception: {e}", level = 'INFO')
                self.sorted_elprices_tomorrow = []
            else:
                self.sorted_elprices_tomorrow = sorted(self.sorted_elprices_tomorrow)


    def getContinuousCheapestTime(self,
        hoursTotal:int = 1,
        calculateBeforeNextDayPrices:bool = False,
        startTime = datetime.datetime.today().hour,
        finishByHour:int = 8
    ):
        """ Returns starttime, endtime and price for cheapest continuous hours with different options depenting on time the call was made
        """

        finishByHour += 1
        h = math.floor(hoursTotal)
        if h == 0:
            h = 1
        if (
            self.ADapi.now_is_between('13:00:00', '23:59:59')
            and len(self.elpricestoday) >= 47 # Looses one hour when starting summertime
        ):
            finishByHour += 24
        elif (
            self.ADapi.now_is_between('06:00:00', '15:00:00')
            and len(self.elpricestoday) == 24
        ):
            if not calculateBeforeNextDayPrices:
                return None, None, self.sorted_elprices_today[h]
            else:
                finishByHour = 15
        elif (
            self.ADapi.now_is_between('15:00:00', '23:59:59')
            and len(self.elpricestoday) < 47
            and self.ADapi.datetime(aware=True) - self.nordpool_last_updated > datetime.timedelta(minutes = 30)
        ):
            """ It can happen that the Nordpool does not update properly with tomorrows prices.
                That has not been tested properly so I'm not sure reloading intergration works.
                One time I had to restart HA for Nordpool integration to get tomorrows prices.
                TODO: Find out what data to trigger reload of Nordpool integration.
            """
            self.nordpool_last_updated = self.ADapi.datetime(aware=True)
            self.ADapi.log(
                f"RELOADS Nordpool integration. Is tomorrows prices valid? {self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow_valid')} : "
                f"{self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'tomorrow')}", level = 'WARNING'
            )

            self.ADapi.call_service('homeassistant/reload_config_entry',
                entity_id = self.nordpool_prices
            )
            
        priceToComplete = 0.0
        avgPriceToComplete = 999.99
        start_of_range = startTime
        if h < finishByHour - start_of_range:
            for check in range(start_of_range, finishByHour - h):
                for hour in range(check, check + h):
                    priceToComplete += self.elpricestoday[hour]
                if priceToComplete < avgPriceToComplete:
                    avgPriceToComplete = priceToComplete
                    startTime = check
                priceToComplete = 0.0
        elif start_of_range < finishByHour:
            divide = 0
            for hour in range(start_of_range, finishByHour ):
                priceToComplete += self.elpricestoday[hour]
                divide += 1
            avgPriceToComplete = priceToComplete / divide

        if startTime < datetime.datetime.today().hour:
            startTime += 24

        runtime = datetime.datetime.today().replace(hour = 0, minute = 0, second = 0, microsecond = 0 ) + datetime.timedelta(hours = startTime)
        endtime = runtime + datetime.timedelta(hours = hoursTotal)
        if runtime.hour == datetime.datetime.today().hour:
            runtime = datetime.datetime.today().replace(second=0, microsecond=0)
        return runtime, endtime, round(avgPriceToComplete/h, 3)


    def findlowprices(self,
        checkhour:int = 1,
        hours:int = 6,
        min_change:float = 0.1
    ) -> float:
        """ Helper function that compares the X hour lowest price to a minimum change and retuns the lowest price
        """

        hours -= 1 # Lists operates 0-23
        if checkhour < 24:
            if self.sorted_elprices_today[hours] > self.sorted_elprices_today[0] + min_change:
                return self.sorted_elprices_today[hours]
            else:
                return self.sorted_elprices_today[0] + min_change
        else:
            if self.sorted_elprices_tomorrow[hours] > self.sorted_elprices_tomorrow[0] + min_change:
                return self.sorted_elprices_tomorrow[hours]
            else:
                return self.sorted_elprices_tomorrow[0] + min_change


    def findpeakhours(self,
        pricedrop:float = 0.3,
        max_continuous_hours:int = 3,
        on_for_minimum:int = 6
    ) -> list:
        """ Finds peak variations in electricity price for saving purposes and returns list with datetime objects
        """
        peak_hours = []
        hour = 0
        length = len(self.elpricestoday) -1
        while hour < length:
                # Checks if price drops more than wanted peak difference
            if self.elpricestoday[hour] - self.elpricestoday[hour+1] >= pricedrop:
                if self.elpricestoday[hour] > self.findlowprices(checkhour = hour, hours = on_for_minimum):
                    peak_hours.append(hour)
                else:
                    countDown = on_for_minimum
                    h = hour +1
                    while (
                        self.elpricestoday[hour] - self.elpricestoday[h] >= pricedrop
                        and h < length
                        and countDown > 0
                    ):
                        h += 1
                        countDown -= 1
                    if countDown == 0:
                        peak_hours.append(hour)
            hour += 1
        
        if not peak_hours:
            hour = 0
            while hour < length -2:
                    # Checks if price drops 2x more than wanted peak difference during 3 hours
                if (
                    self.elpricestoday[hour] - self.elpricestoday[hour+3] >= pricedrop * 1.8
                    and self.elpricestoday[hour+1] > self.findlowprices(checkhour = hour, hours = on_for_minimum)
                ):
                    peak_hours.append(hour+2)
                hour += 1

        if peak_hours:
                # Checks if price increases again during next 3 hours and removes peak
            for peak in peak_hours:
                if peak < len(self.elpricestoday)-3:
                    if (
                        self.elpricestoday[peak] < self.elpricestoday[peak+1]
                        or self.elpricestoday[peak] < self.elpricestoday[peak+2]
                        or self.elpricestoday[peak] < self.elpricestoday[peak+3]
                    ):
                        peak_hours.remove(peak)

        if peak_hours:
                # Finds continuous more expencive hours before peak hour
            peaks_list = peak_hours.copy()
            length_of_peaks_list = len(peak_hours)
            neg_peak_counter_hour = length_of_peaks_list -1
            hour = peaks_list[0]
            last_hour = peaks_list[-1]
            continuous_hours = 0

            while (
                last_hour >= hour
                and neg_peak_counter_hour >= 0
            ):
                if not last_hour in peak_hours:
                    continuous_hours = 0
                peakdiff = pricedrop
                last_hour = peaks_list[neg_peak_counter_hour]
                counter = 0
                h = last_hour 
                hour_list = []

                while (
                    neg_peak_counter_hour >= 0
                    and last_hour == peaks_list[neg_peak_counter_hour]
                    and last_hour >= hour
                ):
                    counter += 1
                    neg_peak_counter_hour -= 1
                    last_hour -= 1
                    continuous_hours += 1
                counter = max_continuous_hours - counter

                while (
                    counter >= 0
                    and continuous_hours < max_continuous_hours
                    and self.elpricestoday[last_hour] > self.elpricestoday[h+1] + peakdiff and
                    last_hour >= 0
                ):
                    if (
                        not last_hour in peak_hours
                        and not last_hour-1 in peak_hours
                        and not last_hour-2 in peak_hours
                    ):
                        hour_list.append(last_hour)
                        last_hour -= 1
                        continuous_hours += 1
                        counter -= 1
                        peakdiff *= 1.05 # Adds a 5% increase in pricedifference pr hour saving
                    else:
                        ch = continuous_hours
                        lh = last_hour
                        nextHoursInPeak = True
                        while (
                            ch < max_continuous_hours
                            and self.elpricestoday[lh] > self.elpricestoday[h+1] + peakdiff
                            and lh >= 0 and nextHoursInPeak
                        ):
                            if (
                                not lh in peak_hours
                                and not lh-1 in peak_hours
                                and not lh-2 in peak_hours
                            ):
                                nextHoursInPeak = False
                                break
                            lh -= 1
                            ch += 1
                            counter -= 1
                        if not nextHoursInPeak:
                            while (
                                counter >= 0
                                and continuous_hours < max_continuous_hours
                                and self.elpricestoday[last_hour] > self.elpricestoday[h+1] + peakdiff
                                and last_hour >= lh+1
                            ):
                                hour_list.append(last_hour)
                                if last_hour in peaks_list:
                                    neg_peak_counter_hour -= 1
                                peakdiff *= 1.05
                                last_hour -= 1
                                continuous_hours += 1

                for num in reversed(hour_list):
                    if not num in peak_hours:
                        peak_hours.append(num)

        peak_hours = sorted(peak_hours)
        peak_times = []
        for t in peak_hours:
            peak_times.append(
                datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
                + datetime.timedelta(hours = t)
            )
        return peak_times


    def findLowPriceHours(self,
        priceincrease:float = 0.6,
        max_continuous_hours:int = 2
    ) -> list:
        """ Finds low price variations in electricity price for spending purposes and returns list with datetime objects
        """

        cheap_hours = []
        hour = 1
        length = len(self.elpricestoday) -2

        while hour < length:
                # Checks if price increases more than wanted peak difference
            if (
                self.elpricestoday[hour+1] - self.elpricestoday[hour] >= priceincrease
                and self.elpricestoday[hour] <= self.findlowprices(hour, 3, 0.08)
            ):
                cheap_hours.append(hour)
                if self.elpricestoday[hour-1] < self.elpricestoday[hour] + 0.06:
                    cheap_hours.append(hour-1)
                hour += 1
                # Checks if price increases x1,4 peak difference during two hours
            elif (
                self.elpricestoday[hour+1] - self.elpricestoday[hour] >= (priceincrease * 0.6)
                and self.elpricestoday[hour+1] - self.elpricestoday[hour-1] >= (priceincrease * 1.4)
                and self.elpricestoday[hour-1] <= self.findlowprices(hour, 3, 0.1)
            ):
                cheap_hours.append(hour-1)
                if self.elpricestoday[hour-2] < self.elpricestoday[hour-1] + 0.06:
                    cheap_hours.append(hour-2)
            hour += 1
        cheap_hours = sorted(cheap_hours)
        cheap_times = []

        for t in cheap_hours:
            if not datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours = t) in cheap_times:
                cheap_times.append(datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours = t))
        return cheap_times


    def continuousHoursOff(self, peak_hours:list = []):
        """ Returns how many hours continiously peak hours turn something off/down for savings
            and the time it turns on
        """

        off_hours:int = 0
        max_off_hours:int = 0
        turn_on_at = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
        for t in peak_hours:
            if (
                not t - datetime.timedelta(hours = 1) in peak_hours
                and t in peak_hours
            ):
                off_hours = 1
            elif (
                t in peak_hours
                and not t + datetime.timedelta(hours = 1) in peak_hours
            ):
                off_hours += 1
                if max_off_hours < off_hours:
                    max_off_hours = off_hours
                    turn_on_at = t + datetime.timedelta(hours = 1)
            elif datetime.datetime.today().day == t.day:
                off_hours += 1
            else:
                break
        return max_off_hours, turn_on_at


    def print_peaks(self, peak_hours:list = []) -> None:
        """ Formats hours list to readable string for easy logging/testing of settings
        """

        print_peak_hours:str = ''
        for t in peak_hours:
            if (
                t - datetime.timedelta(hours = 1) in peak_hours
                and t + datetime.timedelta(hours = 1) in peak_hours
            ):
                continue
            hour = (t - datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 3600
            if hour < 24:
                print_peak_hours += str(f"{self.currency} {self.elpricestoday[int(hour)]} today at {int(hour)}")
            else:
                print_peak_hours += str(f"{self.currency} {self.elpricestoday[int(hour)]} tomorrow at {int(hour)-24}")
            if (
                not t - datetime.timedelta(hours = 1) in peak_hours
                and t + datetime.timedelta(hours = 1) in peak_hours
            ):
                print_peak_hours += " until "
            elif (
                t - datetime.timedelta(hours = 1) in peak_hours
                and not t + datetime.timedelta(hours = 1) in peak_hours
            ):
                if hour < 24:
                    print_peak_hours += str(
                        f". Goes back to normal at {int(hour)+1} {self.currency} {self.elpricestoday[int(hour)+1]}."
                    )
                else:
                    print_peak_hours += str(
                        f". Goes back to normal tomorrow at {int(hour)-23} {self.currency} {self.elpricestoday[int(hour)+1]}. "
                    )
            else:
                print_peak_hours += ". "
        return print_peak_hours



class ElectricalUsage(hass.Hass):

    """ Main class of ElectricalManagement

        @Pythm / https://github.com/Pythm
    """

    def initialize(self):
        self.chargers:list = []
        self.appliances:list = []
        self.heaters:list = []

        global RECIPIENTS
        RECIPIENTS = self.args.get('notify_receiver', [])

        if 'workday' in self.args:
            workday_sensor = self.args['workday']
        else:
            workday_sensor = 'binary_sensor.workday_sensor'
            if not self.entity_exists(self.get_entity(self.workday)):
                self.set_state(self.workday, state = 'on')
                self.log(
                    "'workday' binary_sensor not defined in app configuration or found in Home Assistant. "
                    "Will only use Saturdays and Sundays as nighttax and not Holidays. "
                    "Please install workday sensor from: https://www.home-assistant.io/integrations/workday/ "
                    "to calculate nighttime tax during hollidays",
                    level = 'INFO'
                )

        global ELECTRICITYPRICE
        ELECTRICITYPRICE = ElectricityPrice(self,
        nordpool = self.args.get('nordpool',None),
        daytax = self.args.get('daytax',0),
        nighttax = self.args.get('nighttax',0),
        workday = workday_sensor,
        power_support_above = self.args.get('power_support_above', 10),
        support_amount = self.args.get('support_amount', 0)
    )


            # Consumption sensors
        self.current_consumption = self.args.get('power_consumption', None) # Watt
        if not self.current_consumption:
            raise Exception (
                "power_consumption sensor not provided in configuration. Aborting Electrical Usage setup. "
                "Please provide a watt power consumption sensor to use this function"
            )
        try:
            float(self.get_state(self.current_consumption))
        except Exception as e:
            self.log(
                f"power_consumption sensor is not a number. Please provide a watt power consumption sensor for this function",
                level = 'WARNING'
            )
            self.log(
                "If power_consumption should be a number and this error occurs after HA restart, your sensor is probably not started sending data",
                level = 'INFO'
            )
            self.log(e, level = 'DEBUG')

        sensor_states = None
        if 'accumulated_consumption_current_hour' in self.args:
            self.accumulated_consumption_current_hour = self.args['accumulated_consumption_current_hour'] # kWh
        else:
            sensor_states = self.get_state(entity='sensor')
            for sensor_id, sensor_states in sensor_states.items():
                if 'accumulated_consumption_current_hour' in sensor_id:
                    self.accumulated_consumption_current_hour = sensor_id
                    break

        if not self.accumulated_consumption_current_hour:
            self.log(
                "accumulated_consumption_current_hour not found. "
                "Please install Tibber Pulse or input equivialent to provide kWh consumption current hour.",
                level = 'WARNING'
            )
            self.log(
                "Check out https://tibber.com/ to learn more. "
                "If you are interested in switchin to Tibber you can use my invite link to get a startup bonus: "
                "https://invite.tibber.com/fydzcu9t",
                level = 'INFO'
            )
            raise Exception (
                "accumulated_consumption_current_hour not found. "
                "Please install Tibber Pulse or input equivialent to provide kWh consumption current hour."
            )
        else:
            attr_last_updated = self.get_state(entity_id=self.accumulated_consumption_current_hour, attribute="last_updated")
            if not attr_last_updated:
                self.log(
                    f"{self.get_state(self.accumulated_consumption_current_hour)} has no 'last_updated' attribute. Function might fail",
                    level = 'INFO'
                )

            # Production sensors
        self.current_production = self.args.get('power_production', None) # Watt
        self.accumulated_production_current_hour = self.args.get('accumulated_production_current_hour', None) # Watt

            # Setting buffer for kWh usage
        self.buffer:float = self.args.get('buffer', 0.4)
        #self.buffer += 0.02 # Correction of calculation
        self.max_kwh_goal:int = self.args.get('max_kwh_goal', 5)


            # Establish and recall persistent data using JSON
        global JSON_PATH
        JSON_PATH = self.args.get('json_path', None)
        if not JSON_PATH:
            raise Exception (
                "Path to store json not provided. Please input a valid path with configuration 'json_path' "
            )
        ElectricityData:dict = {}
        try:
            with open(JSON_PATH, 'r') as json_read:
                ElectricityData = json.load(json_read)
        except FileNotFoundError:
            ElectricityData = {"MaxUsage" : {"max_kwh_usage_pr_hour": self.max_kwh_goal, "topUsage" : [0,0,0]},
                            "charger" : {},
                            "consumption" : {"idleConsumption" : {"ConsumptionData" : {}}}}
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
            self.log(
                f"Json file created at {JSON_PATH}",
                level = 'INFO'
            )

        self.max_kwh_usage_pr_hour:int = ElectricityData['MaxUsage']['max_kwh_usage_pr_hour']
        newTotal:float = 0.0
        self.top_usage_hour:float = ElectricityData['MaxUsage']['topUsage'][0] # Lowest of top 3 consumption hours. Used to log, if higher.


            # Default vacation state for saving purposes when away from home for longer periodes
        if 'away_state' in self.args: # Old name...
            self.away_state = self.args['away_state']
        elif 'vacation' in self.args:
            self.away_state = self.args['vacation']
        else:
            self.away_state = 'input_boolean.vacation'
            if not self.entity_exists(self.get_entity(self.away_state)):
                self.set_state(self.away_state, state = 'off')
            else:
                self.log(
                    "'vacation' not configured. Using 'input_boolean.vacation' as default away state",
                    level = 'WARNING'
                )


            # Weather sensors
        global RAIN_AMOUNT
        global WIND_AMOUNT

        self.weather_temperature = None
        self.outside_temperature = self.args.get('outside_temperature', None)
        self.rain_sensor = self.args.get('rain_sensor', None)
        self.rain_level:float = self.args.get('rain_level',3)
        self.anemometer = self.args.get('anemometer', None)
        self.anemometer_speed:int = self.args.get('anemometer_speed',40)
        sensor_states = self.get_state(entity='weather')
        for sensor_id, sensor_states in sensor_states.items():
            if 'weather.' in sensor_id:
                self.weather_temperature = sensor_id
        if (
            not self.outside_temperature
            and not self.weather_temperature
        ):
            self.log(
                "Outside temperature not found. Please provide sensors or install Met.no in Home Assistant. "
                "https://www.home-assistant.io/integrations/met/",
                level = 'WARNING'
            )

        if self.rain_sensor:
            self.listen_state(self.rainSensorUpdated, self.rain_sensor)
            try:
                RAIN_AMOUNT = float(self.get_state(self.rain_sensor))
            except ValueError as ve:
                self.log(f"Not able to set rain amount. {ve}", level = 'DEBUG')
            except Exception as e:
                self.log(f"Not able to set rain amount from {self.rain_sensor}. {e}", level = 'INFO')

        if self.anemometer:
            self.listen_state(self.anemometerUpdated, self.anemometer)
            try:
                WIND_AMOUNT = float(self.get_state(self.anemometer))
            except ValueError as ve:
                self.log(f"Not able to set wind amount. {ve}", level = 'DEBUG')
            except Exception as e:
                self.log(f"Not able to set wind amount from {self.anemometer}. {e}", level = 'INFO')
 

        global OUT_TEMP
        try:
            OUT_TEMP = float(self.get_state(self.outside_temperature))
            self.listen_state(self.outsideTemperatureUpdated, self.outside_temperature)
            if self.weather_temperature:
                self.listen_state(self.outsideBackupTemperatureUpdated, self.weather_temperature,
                    attribute = 'temperature'
                )
        except (ValueError, TypeError) as ve:
            OUT_TEMP = float(self.get_state(entity_id = self.weather_temperature, attribute = 'temperature'))
            self.listen_state(self.outsideBackupTemperatureUpdated, self.weather_temperature,
                attribute = 'temperature'
            )
            self.log(
                f"Outside temperature is not configured or down at the moment. "
                f"Using {self.weather_temperature} for outside temperature. "
                f"It is now {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')} degrees outside. "
                f"Error: {ve}",
                level = 'INFO'
            )
        except Exception as e:
            self.log(
                "Outside temperature is not a number. Please provide sensors in configuration or install Met.no in Home Assistant. "
                "https://www.home-assistant.io/integrations/met/",
                level = 'INFO'
            )
            self.log(f" {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')} {e}", level = 'INFO')


            # Set up chargers
        self.informEveryChange:bool = False
        if 'options' in self.args:
            if 'informEveryChange' in self.args['options']:
                self.informEveryChange = True

        global CHARGE_SCHEDULER
        CHARGE_SCHEDULER = Scheduler(self,
            informEveryChange = self.informEveryChange,
            stopAtPriceIncrease = self.args.get('stopAtPriceIncrease', 0.3),
            startBeforePrice = self.args.get('startBeforePrice', 0.01),
            infotext = self.args.get('infotext', None)
        )

        self.queueChargingList:list = [] # Cars/chargers currently charging.
        self.solarChargingList:list = [] # Cars/chargers currently charging.


        # Setting up generic chargers
        chargers = self.args.get('charger', [])
        for t in chargers:
            location_tracker = t.get('location_tracker',None)
            if not location_tracker:
                raise Exception (
                    "location_tracker sensor not provided in configuration for charger. Aborting Charger setup. "
                    "Please provide a location_tracker sensor to use this function"
                )
            namespace = t.get('namespace',None)
            name = t.get('name',None)
            charger_sensor = t.get('charger_sensor',None)

            Car1 = Car(self,
                namespace = namespace,
                carName = name,
                charger_sensor = charger_sensor,
                charge_limit = t.get('charge_limit',None),
                battery_sensor = t.get('battery_sensor',None),
                asleep_sensor = t.get('asleep_sensor', None),
                online_sensor = t.get('online_sensor',None),
                location_tracker = location_tracker,
                destination_location_tracker = t.get('destination_location_tracker',None),
                arrival_time = t.get('arrival_time',None),
                software_update = t.get('software_update',None),
                force_data_update = t.get('force_data_update', None),
                polling_switch = t.get('polling_switch',None),
                data_last_update_time = t.get('data_last_update_time',None),
                battery_size = t.get('battery_size',100),
                pref_charge_limit = t.get('pref_charge_limit',90)
            )

            Charger1 = Charger(self,
                Car = Car1,
                namespace = namespace,
                charger = name,
                charger_id = t.get('charger_id', None),
                charger_sensor = charger_sensor,
                charger_switch = t.get('charger_switch',None),
                charging_amps = t.get('charging_amps',None),
                charger_power = t.get('charger_power',None),
                session_energy = t.get('session_energy', None),
                volts = t.get('volts', None),
                phases = t.get('phases', None),
                priority = t.get('priority',3),
                finishByHour = t.get('finishByHour',None),
                charge_now = t.get('charge_now',None),
                charge_on_solar = t.get('charge_on_solar',None),
                departure = t.get('departure',None),
                guest = t.get('guest', None)
            )

            self.chargers.append(Charger1)


        # Setting up Tesla cars using Tesla API to control charging
        teslas = self.args.get('tesla', [])
        for t in teslas:
            namespace = t.get('namespace',None)
            charger_sensor = t.get('charger_sensor',None)
            charger_switch = t.get('charger_switch',None)
            charging_amps = t.get('charging_amps',None)
            charger_power = t.get('charger_power',None)
            charge_limit = t.get('charge_limit',None)
            session_energy = t.get('session_energy', None)
            asleep_sensor = t.get('asleep_sensor', None)
            online_sensor = t.get('online_sensor',None)
            battery_sensor = t.get('battery_sensor',None)
            location_tracker = t.get('location_tracker',None)
            destination_location_tracker = t.get('destination_location_tracker',None)
            arrival_time = t.get('arrival_time',None)
            software_update = t.get('software_update',None)
            force_data_update = t.get('force_data_update', None)
            polling_switch = t.get('polling_switch',None)
            data_last_update_time = t.get('data_last_update_time',None)

            # Find sensors not provided:
            if 'charger' in t:
                car = t['charger']
            if 'charger_sensor' in t:
                charger_sensor:str = t['charger_sensor']
                name = charger_sensor.replace(charger_sensor,'binary_sensor.','')
                name = name.replace(name,'_charger','')
                car = name

            sensor_states = self.get_state(entity='sensor')
            for sensor_id, sensor_states in sensor_states.items():

                if 'binary_sensor.' + car + '_charger' in sensor_id:
                    if not charger_sensor:
                        charger_sensor = sensor_id
                if 'switch.' + car + '_charger' in sensor_id:
                    if not charger_switch:
                        charger_switch = sensor_id
                if 'number.' + car + '_charging_amps' in sensor_id:
                    if not charging_amps:
                        charging_amps = sensor_id
                if 'sensor.' + car + '_charger_power' in sensor_id:
                    if not charger_power:
                        charger_power = sensor_id
                if 'number.' + car + '_charge_limit' in sensor_id:
                    if not charge_limit:
                        charge_limit = sensor_id
                if 'sensor.' + car + '_energy_added' in sensor_id:
                    if not session_energy:
                        session_energy = sensor_id
                if 'binary_sensor.' + car + '_asleep' in sensor_id:
                    if not asleep_sensor:
                        asleep_sensor = sensor_id
                if 'binary_sensor.' + car + '_online' in sensor_id:
                    if not online_sensor:
                        online_sensor = sensor_id
                if 'sensor.' + car + '_battery' in sensor_id:
                    if not battery_sensor:
                        battery_sensor = sensor_id
                if 'device_tracker.' + car + '_location_tracker' in sensor_id:
                    if not location_tracker:
                        location_tracker = sensor_id
                if 'device_tracker.' + car + '_destination_location_tracker' in sensor_id:
                    if not destination_location_tracker:
                        destination_location_tracker = sensor_id
                if 'sensor.' + car + '_arrival_time' in sensor_id:
                    if not arrival_time:
                        arrival_time = sensor_id
                if 'update.' + car + '_software_update' in sensor_id:
                    if not software_update:
                        software_update = sensor_id
                if 'button.' + car + '_force_data_update' in sensor_id:
                    if not force_data_update:
                        force_data_update = sensor_id
                if 'switch.' + car + '_polling' in sensor_id:
                    if not polling_switch:
                        polling_switch = sensor_id
                if 'sensor.' + car + '_data_last_update_time' in sensor_id:
                    if not data_last_update_time:
                        data_last_update_time = sensor_id

            if not charger_sensor:
                raise Exception (
                    f"charger_sensor not defined or found. Please provide 'charger_sensor' in args for {car}"
                )
            if not charger_switch:
                raise Exception (
                    f"charger_switch not defined or found. Please provide 'charger_switch' in args for {car}"
                )
            if not charging_amps:
                raise Exception (
                    f"charging_amps not defined or found. Please provide 'charging_amps' in args for {car}"
                )
            if not charger_power:
                raise Exception (
                    f"charger_power not defined or found. Please provide 'charger_power' in args for {car}"
                )
            if not charge_limit:
                raise Exception (
                    f"charge_limit not defined or found. Please provide 'charge_limit' in args for {car}"
                )
            if not asleep_sensor:
                raise Exception (
                    f"asleep_sensor not defined or found. Please provide 'asleep_sensor' in args for {car}"
                )
            if not online_sensor:
                raise Exception (
                    f"online_sensor not defined or found. Please provide 'online_sensor' in args for {car}"
                )
            if not battery_sensor:
                raise Exception (
                    f"battery_sensor not defined or found. Please provide 'battery_sensor' in args for {car}"
                )
            if not location_tracker:
                raise Exception (
                    f"location_tracker not defined or found. Please provide 'location_tracker' in args for {car}"
                )
            if not destination_location_tracker:
                raise Exception (
                    f"destination_location_tracker not defined or found. Please provide 'destination_location_tracker' "
                    f"in args for {car}"
                )
            if not arrival_time:
                raise Exception (
                    f"arrival_time not defined or found. Please provide 'arrival_time' in args for {car}"
                )
            if not software_update:
                raise Exception (
                    f"software_update not defined or found. Please provide 'software_update' in args for {car}"
                )
            if not force_data_update:
                raise Exception (
                    f"force_data_update not defined or found. Please provide 'force_data_update' in args for {car}"
                )
            if not polling_switch:
                raise Exception (
                    f"polling_switch not defined or found. Please provide 'polling_switch' in args for {car}"
                )
            if not data_last_update_time:
                raise Exception (
                    f"force_data_update not defined or found. Please provide 'force_data_update' in args for {car}"
                )

            teslaCar = Tesla_car(self,
                namespace = namespace,
                carName = car,
                charger_sensor = charger_sensor,
                charge_limit = charge_limit,
                battery_sensor = battery_sensor,
                asleep_sensor = asleep_sensor,
                online_sensor = online_sensor,
                location_tracker = location_tracker,
                destination_location_tracker = destination_location_tracker,
                arrival_time = arrival_time,
                software_update = software_update,
                force_data_update = force_data_update,
                polling_switch = polling_switch,
                data_last_update_time = data_last_update_time,
                battery_size = t.get('battery_size',100),
                pref_charge_limit = t.get('pref_charge_limit',90)
            )

            teslaCharger = Tesla_charger(self,
                Car = teslaCar,
                namespace = namespace,
                charger = car,
                charger_sensor = charger_sensor,
                charger_switch = charger_switch,
                charging_amps = charging_amps,
                charger_power = charger_power,
                session_energy = session_energy,
                priority = t.get('priority',3),
                finishByHour = t.get('finishByHour',None),
                charge_now = t.get('charge_now',None),
                charge_on_solar = t.get('charge_on_solar',None),
                departure = t.get('departure',None),
                guest = None
            )

            self.chargers.append(teslaCharger)


        # Setting up Easee charger with a car without API to control charging
        easees = self.args.get('easee', [])
        for e in easees:
            namespace = e.get('namespace',None)
            charger_status = e.get('charger_status',None)
            reason_for_no_current = e.get('reason_for_no_current',None)
            current = e.get('current',None)
            charger_power = e.get('charger_power',None)
            voltage = e.get('voltage',None)
            max_charger_limit = e.get('max_charger_limit',None)
            online_sensor = e.get('online_sensor',None)
            session_energy = e.get('session_energy',None)

            # Find sensors not provided:
            if 'charger' in e:
                charger = e['charger']
            if 'charger_status' in e:
                charger_status:str = e['charger_status']
                name = charger_status.replace(charger_status,'sensor.','')
                name = name.replace(name,'_status','')
                charger = name

            sensor_states = self.get_state(entity='sensor')
            for sensor_id, sensor_states in sensor_states.items():
                if 'sensor.' + charger + '_status' in sensor_id:
                    if not charger_status:
                        charger_status = sensor_id
                if 'sensor.' + charger + '_reason_for_no_current' in sensor_id:
                    if not reason_for_no_current:
                        reason_for_no_current = sensor_id
                if 'sensor.' + charger + '_current' in sensor_id:
                    if not current:
                        current = sensor_id
                if 'sensor.' + charger + '_power' in sensor_id:
                    if not charger_power:
                        charger_power = sensor_id
                if 'sensor.' + charger + '_voltage' in sensor_id:
                    if not voltage:
                        voltage = sensor_id
                if 'sensor.' + charger + '_max_charger_limit' in sensor_id:
                    if not max_charger_limit:
                        max_charger_limit = sensor_id
                if 'binary_sensor.' + charger + '_online' in sensor_id:
                    if not online_sensor:
                        online_sensor = sensor_id
                if 'sensor.' + charger + '_session_energy' in sensor_id:
                    if not session_energy:
                        session_energy = sensor_id

            if not charger_status:
                raise Exception (
                    f"charger_status not defined or found. Please provide 'charger_status' in args for {charger}"
                )
            if not reason_for_no_current:
                raise Exception (
                    f"reason_for_no_current not defined or found. Please enable 'reason_for_no_current' "
                    f"sensor in Easee integration for {charger}"
                )
            if not current:
                raise Exception (
                    f"current not defined or found. Please enable 'current' sensor in Easee integration for {charger}"
                )
            if not charger_power:
                raise Exception (
                    f"charger_power not defined or found. Please enable 'charger_power' sensor in Easee integration for {charger}"
                )
            if not voltage:
                raise Exception (
                    f"voltage not defined or found. Please enable 'voltage' sensor in Easee integration for {charger}"
                )
            if not max_charger_limit:
                raise Exception (
                    f"max_charger_limit not defined or found. Please enable 'max_charger_limit' sensor in Easee integration for {charger}"
                )
            if not online_sensor:
                raise Exception (
                    f"online_sensor not defined or found. Please provide 'online_sensor' in args for {charger}"
                )
            if not session_energy:
                raise Exception (
                    f"session_energy not defined or found. Please enable 'session_energy' sensor in Easee integration for {charger}"
                )

            car1 = Car(self,
                namespace = namespace,
                carName = e.get('carName',charger),
                charger_sensor = e.get('charger_sensor',None),
                charge_limit = e.get('charge_limit',None),
                battery_sensor = e.get('battery_sensor',None),
                asleep_sensor = e.get('asleep_sensor',None),
                online_sensor = e.get('online_sensor',online_sensor),
                location_tracker = e.get('location_tracker',None),
                destination_location_tracker = e.get('destination_location_tracker',None),
                arrival_time = e.get('arrival_time',None),
                software_update = e.get('software_update',None),
                force_data_update = e.get('force_data_update',None),
                polling_switch = e.get('polling_switch',None),
                data_last_update_time = e.get('data_last_update_time',None),
                battery_size = e.get('battery_size',None),
                pref_charge_limit = e.get('pref_charge_limit',100)
            )

            easeeCharger = Easee(self,
                Car = car1,
                namespace = namespace,
                charger = charger,
                charger_sensor = charger_status,
                reason_for_no_current = reason_for_no_current,
                charging_amps = current,
                charger_power = charger_power,
                session_energy = session_energy,
                voltage = voltage,
                max_charger_limit = max_charger_limit,
                priority = e.get('priority',3),
                finishByHour = e.get('finishByHour',None),
                charge_now = e.get('charge_now',None),
                charge_on_solar = e.get('charge_on_solar',None),
                departure = e.get('departure',None),
                guest = e.get('guest',None)
            )
            self.chargers.append(easeeCharger)


        # Setting up Easee charger with Tesla car using Easee API to control charging
        easee_tesla = self.args.get('easee_tesla', [])
        for e in easee_tesla:
            namespace = e.get('namespace',None)
            charger_status = e.get('charger_status',None)
            reason_for_no_current = e.get('reason_for_no_current',None)
            current = e.get('current',None)
            charger_power = e.get('charger_power',None)
            voltage = e.get('voltage',None)
            max_charger_limit = e.get('max_charger_limit',None)
            online_sensor = e.get('online_sensor',None)
            session_energy = e.get('session_energy',None)

            charger_sensor = e.get('charger_sensor',None)
            #charger_switch = e.get('charger_switch',None)
            #charging_amps = e.get('charging_amps',None)
            charge_limit = e.get('charge_limit',None)
            asleep_sensor = e.get('asleep_sensor', None)
            battery_sensor = e.get('battery_sensor',None)
            location_tracker = e.get('location_tracker',None)
            destination_location_tracker = e.get('destination_location_tracker',None)
            arrival_time = e.get('arrival_time',None)
            software_update = e.get('software_update',None)
            force_data_update = e.get('force_data_update', None)
            polling_switch = e.get('polling_switch',None)
            data_last_update_time = e.get('data_last_update_time',None)

            # Find sensors not provided:
            if 'car' in e:
                car = e['car']
            if 'charger_sensor' in e:
                charger_sensor:str = e['charger_sensor']
                name = charger_sensor.replace(charger_sensor,'binary_sensor.','')
                name = name.replace(name,'_charger','')
                car = name

            if 'charger' in e:
                charger = e['charger']
            if 'charger_status' in e:
                charger_status:str = e['charger_status']
                name = charger_status.replace(charger_status,'sensor.','')
                name = name.replace(name,'_status','')
                charger = name

            sensor_states = self.get_state(entity='sensor')
            for sensor_id, sensor_states in sensor_states.items():

                if 'binary_sensor.' + car + '_charger' in sensor_id:
                    if not charger_sensor:
                        charger_sensor = sensor_id
                if 'number.' + car + '_charge_limit' in sensor_id:
                    if not charge_limit:
                        charge_limit = sensor_id
                if 'binary_sensor.' + car + '_asleep' in sensor_id:
                    if not asleep_sensor:
                        asleep_sensor = sensor_id
                if 'binary_sensor.' + car + '_online' in sensor_id:
                    if not online_sensor:
                        online_sensor = sensor_id
                if 'sensor.' + car + '_battery' in sensor_id:
                    if not battery_sensor:
                        battery_sensor = sensor_id
                if 'device_tracker.' + car + '_location_tracker' in sensor_id:
                    if not location_tracker:
                        location_tracker = sensor_id
                if 'device_tracker.' + car + '_destination_location_tracker' in sensor_id:
                    if not destination_location_tracker:
                        destination_location_tracker = sensor_id
                if 'sensor.' + car + '_arrival_time' in sensor_id:
                    if not arrival_time:
                        arrival_time = sensor_id
                if 'update.' + car + '_software_update' in sensor_id:
                    if not software_update:
                        software_update = sensor_id
                if 'button.' + car + '_force_data_update' in sensor_id:
                    if not force_data_update:
                        force_data_update = sensor_id
                if 'switch.' + car + '_polling' in sensor_id:
                    if not polling_switch:
                        polling_switch = sensor_id
                if 'sensor.' + car + '_data_last_update_time' in sensor_id:
                    if not data_last_update_time:
                        data_last_update_time = sensor_id

                if 'sensor.' + charger + '_status' in sensor_id:
                    if not charger_status:
                        charger_status = sensor_id
                if 'sensor.' + charger + '_reason_for_no_current' in sensor_id:
                    if not reason_for_no_current:
                        reason_for_no_current = sensor_id
                if 'sensor.' + charger + '_current' in sensor_id:
                    if not current:
                        current = sensor_id
                if 'sensor.' + charger + '_power' in sensor_id:
                    if not charger_power:
                        charger_power = sensor_id
                if 'sensor.' + charger + '_voltage' in sensor_id:
                    if not voltage:
                        voltage = sensor_id
                if 'sensor.' + charger + '_max_charger_limit' in sensor_id:
                    if not max_charger_limit:
                        max_charger_limit = sensor_id
                if 'sensor.' + charger + '_session_energy' in sensor_id:
                    if not session_energy:
                        session_energy = sensor_id

            if not charger_status:
                raise Exception (
                    f"charger_status not defined or found. Please provide 'charger_status' in args for {charger}"
                )
            if not reason_for_no_current:
                raise Exception (
                    f"reason_for_no_current not defined or found. Please enable 'reason_for_no_current' "
                    f"sensor in Easee integration for {charger}"
                )
            if not current:
                raise Exception (
                    f"current not defined or found. Please enable 'current' sensor in Easee integration for {charger}"
                )
            if not charger_power:
                raise Exception (
                    f"charger_power not defined or found. Please enable 'charger_power' sensor in Easee integration for {charger}"
                )
            if not voltage:
                raise Exception (
                    f"voltage not defined or found. Please enable 'voltage' sensor in Easee integration for {charger}"
                )
            if not max_charger_limit:
                raise Exception (
                    f"max_charger_limit not defined or found. Please enable 'max_charger_limit' sensor in Easee integration for {charger}"
                )
            if not online_sensor:
                raise Exception (
                    f"online_sensor not defined or found. Please provide 'online_sensor' in args for {car}"
                )
            if not session_energy:
                raise Exception (
                    f"session_energy not defined or found. Please enable 'session_energy' sensor in Easee integration for {charger}"
                )
            if not charger_sensor:
                raise Exception (
                    f"charger_sensor not defined or found. Please provide 'charger_sensor' in args for {car}"
                )
            if not charge_limit:
                raise Exception (
                    f"charge_limit not defined or found. Please provide 'charge_limit' in args for {car}"
                )
            if not asleep_sensor:
                raise Exception (
                    f"asleep_sensor not defined or found. Please provide 'asleep_sensor' in args for {car}"
                )
            if not battery_sensor:
                raise Exception (
                    f"battery_sensor not defined or found. Please provide 'battery_sensor' in args for {car}"
                )
            if not location_tracker:
                raise Exception (
                    f"location_tracker not defined or found. Please provide 'location_tracker' in args for {car}"
                )
            if not destination_location_tracker:
                raise Exception (
                    f"destination_location_tracker not defined or found. Please provide 'destination_location_tracker' "
                    f"in args for {car}"
                )
            if not arrival_time:
                raise Exception (
                    f"arrival_time not defined or found. Please provide 'arrival_time' in args for {car}"
                )
            if not software_update:
                raise Exception (
                    f"software_update not defined or found. Please provide 'software_update' in args for {car}"
                )
            if not force_data_update:
                raise Exception (
                    f"force_data_update not defined or found. Please provide 'force_data_update' in args for {car}"
                )
            if not polling_switch:
                raise Exception (
                    f"polling_switch not defined or found. Please provide 'polling_switch' in args for {car}"
                )
            if not data_last_update_time:
                raise Exception (
                    f"force_data_update not defined or found. Please provide 'force_data_update' in args for {car}"
                )

            teslaCar = Tesla_car(self,
                namespace = namespace,
                carName = car,
                charger_sensor = charger_sensor,
                charge_limit = charge_limit,
                battery_sensor = battery_sensor,
                asleep_sensor = asleep_sensor,
                online_sensor = online_sensor,
                location_tracker = location_tracker,
                destination_location_tracker = destination_location_tracker,
                arrival_time = arrival_time,
                software_update = software_update,
                force_data_update = force_data_update,
                polling_switch = polling_switch,
                data_last_update_time = data_last_update_time,
                battery_size = t.get('battery_size',100),
                pref_charge_limit = t.get('pref_charge_limit',90)
            )

            easeeCharger = Easee(self,
                Car = teslaCar,
                namespace = namespace,
                charger = charger,
                charger_sensor = charger_status,
                reason_for_no_current = reason_for_no_current,
                charging_amps = current,
                charger_power = charger_power,
                session_energy = session_energy,
                voltage = voltage,
                max_charger_limit = max_charger_limit,
                priority = e.get('priority',3),
                finishByHour = e.get('finishByHour',None),
                charge_now = e.get('charge_now',None),
                charge_on_solar = e.get('charge_on_solar',None),
                departure = e.get('departure',None),
                guest = e.get('guest',None)
            )

            self.chargers.append(easeeCharger)


            # Set up hot water boilers and electrical heaters
        self.heatersRedusedConsumption:list = [] # Heaters currently turned off/down due to overconsumption

        heaters = self.args.get('climate', {})
        for heater in heaters:
            if 'name' in heater:
                sensor_states = self.get_state(entity='sensor')
                for sensor_id, sensor_states in sensor_states.items():
                    if 'climate.' + heater['name'] in sensor_id:
                        if not 'heater' in heater:
                            heater['heater'] = sensor_id
                    if (
                        'sensor.' + heater['name'] + '_electric_consumption_w' in sensor_id
                        or 'sensor.' + heater['name'] + '_electric_consumed_w' in sensor_id
                    ):
                        if not 'consumptionSensor' in heater:
                            heater['consumptionSensor'] = sensor_id
                    if (
                        'sensor.' + heater['name'] + '_electric_consumption_kwh' in sensor_id
                        or 'sensor.' + heater['name'] + '_electric_consumed_kwh' in sensor_id
                    ):
                        if not 'kWhconsumptionSensor' in heater:
                            heater['kWhconsumptionSensor'] = sensor_id
                    if 'sensor.' + heater['name'] + '_air_temperature' in sensor_id:
                        if not 'indoor_sensor_temp' in heater:
                            heater['indoor_sensor_temp'] = sensor_id
                            self.log(
                                f"Using built in sensor {heater['indoor_sensor_temp']} as 'indoor_sensor_temp'",
                                level = 'INFO'
                            )

            if not 'heater' in heater:
                self.log(
                    f"'heater' not found or configured in {heater} climate configuration. Climate control setup aborted",
                    level = 'WARNING'
                )
                continue

            if not 'consumptionSensor' in heater:
                heatername = (str(heater['heater'])).split('.')
                heater['consumptionSensor'] = 'input_number.' + heatername[1] + '_power'
                if not self.entity_exists(self.get_entity(heater['consumptionSensor'])):
                    powercapability = heater.get('power', 300)
                    self.set_state(heater['consumptionSensor'], state = powercapability)
                self.log(
                    f"'consumptionSensor' not found or configured. Climate electricity control not optimal. "
                    f"Using {heater['consumptionSensor']} as state with power: {self.get_state(heater['consumptionSensor'])}",
                    level = 'WARNING'
                )
                if not 'power' in heater:
                    self.log(f"Set electrical consumption with 'power' in args for heater.", level = 'INFO')

            if not 'kWhconsumptionSensor' in heater:
                heater['kWhconsumptionSensor'] = 'input_number.zero'
                if not self.entity_exists(self.get_entity(heater['kWhconsumptionSensor'])):
                    self.set_state(heater['kWhconsumptionSensor'], state = 0)
                self.log(
                    "'kWhconsumptionSensor' not found or configured. Climate electricity logging not available. "
                    "Using input_number.zero as state",
                    level = 'WARNING'
                )

            if not 'vacation' in heater:
                heater['vacation'] = self.away_state

            climate = Climate(self,
                heater = heater['heater'],
                consumptionSensor = heater['consumptionSensor'],
                kWhconsumptionSensor = heater['kWhconsumptionSensor'],
                max_continuous_hours = heater.get('max_continuous_hours', 2),
                on_for_minimum = heater.get('on_for_minimum', 12),
                pricedrop = heater.get('pricedrop', 1),
                namespace = heater.get('namespace', None),
                away = heater['vacation'],
                automate = heater.get('automate', None),
                recipient = heater.get('recipient', None),
                indoor_sensor_temp = heater.get('indoor_sensor_temp', None),
                target_indoor_temp = heater.get('target_indoor_temp', 23),
                rain_level = heater.get('rain_level', self.rain_level),
                anemometer_speed = heater.get('anemometer_speed', self.anemometer_speed),
                low_price_max_continuous_hours = heater.get('low_price_max_continuous_hours', 2),
                priceincrease = heater.get('priceincrease', 1),
                windowsensors = heater.get('windowsensors', []),
                daytime_savings = heater.get('daytime_savings', []),
                temperatures = heater.get('temperatures', [])
            )
            self.heaters.append(climate)


        heater_switches = self.args.get('heater_switches', {})
        for heater_switch in heater_switches:
            if 'name' in heater_switch:
                sensor_states = self.get_state(entity='sensor')
                for sensor_id, sensor_states in sensor_states.items():
                    if 'switch.' + heater_switch['name'] in sensor_id:
                        if not 'switch' in heater_switch:
                            heater_switch['switch'] = sensor_id
                    if (
                        'sensor.' + heater_switch['name'] + '_electric_consumption_w' in sensor_id
                        or 'sensor.' + heater_switch['name'] + '_electric_consumed_w' in sensor_id
                    ):
                        if not 'consumptionSensor' in heater_switch:
                            heater_switch['consumptionSensor'] = sensor_id
                    if (
                        'sensor.' + heater_switch['name'] + '_electric_consumption_kwh' in sensor_id
                        or 'sensor.' + heater_switch['name'] + '_electric_consumed_kwh' in sensor_id
                    ):
                        if not 'kWhconsumptionSensor' in heater_switch:
                            heater_switch['kWhconsumptionSensor'] = sensor_id

            if not 'switch' in heater_switch:
                self.log(
                    "'switch' not found or configured in on_off_switch configuration. "
                    "on_off_switch control setup aborted",
                    level = 'WARNING'
                )
                continue

            if not 'consumptionSensor' in heater_switch:
                heatername = (str(heater_switch['switch'])).split('.')
                heater_switch['consumptionSensor'] = 'input_number.' + heatername[1] + '_power'
                if not self.entity_exists(self.get_entity(heater_switch['consumptionSensor'])):
                    powercapability = heater_switch.get('power', 1000)
                    self.set_state(heater_switch['consumptionSensor'], state = powercapability)
                self.log(
                    f"'consumptionSensor' not found or configured. on_off_switch electricity control not optimal. "
                    f"Using {heater_switch['consumptionSensor']} as state with power: {self.get_state(heater_switch['consumptionSensor'])}",
                    level = 'WARNING'
                )
                if not 'power' in heater_switch:
                    self.log(f"Set electrical consumption with 'power' in args for on_off_switch.", level = 'INFO')

            if not 'kWhconsumptionSensor' in heater_switch:
                heater_switch['kWhconsumptionSensor'] = 'input_number.zero'
                if not self.entity_exists(self.get_entity(heater_switch['kWhconsumptionSensor'])):
                    self.set_state(heater_switch['kWhconsumptionSensor'], state = 0)
                self.log(
                    "'kWhconsumptionSensor' not found or configured. on_off_switch electricity logging not available. "
                    "Using input_number.zero as state",
                    level = 'WARNING'
                )
            if not 'max_continuous_hours' in heater_switch:
                heater_switch['max_continuous_hours'] = 8
            if not 'on_for_minimum' in heater_switch:
                heater_switch['on_for_minimum'] = 8
            if not 'pricedrop' in heater_switch:
                heater_switch['pricedrop'] = 0.3
            if not 'vacation' in heater_switch:
                heater_switch['vacation'] = self.away_state


            on_off_switch = On_off_switch(self,
                heater = heater_switch['switch'],
                consumptionSensor = heater_switch['consumptionSensor'],
                kWhconsumptionSensor = heater_switch['kWhconsumptionSensor'],
                max_continuous_hours = heater_switch['max_continuous_hours'],
                on_for_minimum = heater_switch['on_for_minimum'],
                pricedrop = heater_switch['pricedrop'],
                namespace = heater_switch.get('namespace', None),
                away = heater_switch['vacation'],
                automate = heater_switch.get('automate', None),
                recipient = heater_switch.get('recipient', None)
            )
            self.heaters.append(on_off_switch)


            # Set up appliances with remote start function to run when electricity price is at its lowest
        machines = self.args.get('appliances', {})
        for appliance in machines:
            if 'remote_start' in appliance:
                remote_start = appliance['remote_start']
                if 'program' in appliance:
                    program = appliance['program']
                else:
                    self.log(
                        "'program' not configured in Appliances configuration. Setup aborted. Please provide program to start",
                        level = 'WARNING'
                    )
                    continue
                if 'running_time' in appliance:
                    running_time = appliance['running_time']
                else:
                    running_time = 4
                if 'finishByHour' in appliance:
                    finishByHour = appliance['finishByHour']
                else:
                    finishByHour = 6
                machine = Appliances(self,
                    remote_start = remote_start,
                    program = program,
                    running_time = running_time,
                    finishByHour = finishByHour
                )
                self.appliances.append(machine)


        # Variables for different calculations 
        self.accumulated_unavailable:int = 0
        self.last_accumulated_kWh:float = 0
        self.accumulated_kWh_wasUnavailable:bool = False
        self.SolarProducing_ChangeToZero:bool = False

        self.findCharingNotInQueue()

        runtime = datetime.datetime.now()
        addseconds = (round((runtime.minute*60 + runtime.second)/60)+1)*60
        runtime = runtime.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(seconds=addseconds)

        self.run_every(self.checkElectricalUsage, runtime, 60)
        self.listen_state(self.electricityprices_updated, ELECTRICITYPRICE.nordpool_prices,
            attribute = 'tomorrow',
            duration = 120
        )
        self.listen_event(self.mode_event, "MODE_CHANGE")
        self.run_in(self.calculateIdleConsumption, 10)


        # Updates times to save/charge with new prices available
    def electricityprices_updated(self, entity, attribute, old, new, kwargs) -> None:
        for heater in self.heaters:
            self.run_in(heater.heater_getNewPrices, 1)

        if len(new) > 0:
            self.run_in(self.findConsumptionAfterTurnedBackOn, 10)
            self.run_in(self.calculateIdleConsumption, 20)

            for c in self.chargers:
                if c.Car.getLocation() == 'home':
                    c.Car.wakeMeUp()
                    self.run_in(c.findNewChargeTimeWhen, 300)


    def checkElectricalUsage(self, kwargs) -> None:
        """ Calculate and ajust consumption to stay within kWh limit
            Start charging when time to charge
        """
        global CHARGE_SCHEDULER
        global OUT_TEMP

        accumulated_kWh = self.get_state(self.accumulated_consumption_current_hour)
        current_consumption = self.get_state(self.current_consumption)

        runtime = datetime.datetime.now()
        remaining_minute:int = 60 - int(runtime.minute)

            # Check if consumption sensors is valid
        if (
            current_consumption == 'unavailable'
            or current_consumption == 'unknown'
        ):
            current_consumption:float = 0.0
            with open(JSON_PATH, 'r') as json_read:
                ElectricityData = json.load(json_read)

            if ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
                    # Find closest temp registered with data
                if not out_temp_str in ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                    temp_diff:int = 0
                    closest_temp:int
                    for temps in ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                        if OUT_TEMP > float(temps):
                            if temp_diff != 0:
                                if temp_diff < OUT_TEMP - float(temps):
                                    continue

                            temp_diff = OUT_TEMP - float(temps)
                            closest_temp = temps
                        else:
                            if temp_diff != 0:
                                if temp_diff < float(temps) - OUT_TEMP:
                                    continue

                            temp_diff = float(temps) - OUT_TEMP
                            closest_temp = temps
                    out_temp_str = closest_temp

                current_consumption = float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]['Consumption']) * 2
                #current_consumption += float(ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]['HeaterConsumption'])

            for heater in self.heaters:
                current_consumption += float(self.get_state(heater.consumptionSensor))
            for c in self.chargers:
                if (
                    c.Car.getLocation() == 'home'
                    and c.getChargingState() == 'Charging'
                ):
                    current_consumption += float(self.get_state(c.charging_amps)) * c.voltphase

        else:
            current_consumption = float(current_consumption)

        if (
            accumulated_kWh == 'unavailable'
            or accumulated_kWh == 'unknown'
        ):
            if self.accumulated_unavailable > 5:
                # Will try to reload Home Assistant integration every sixth minute the sensor is unavailable. 
                self.accumulated_unavailable = 0
                self.call_service('homeassistant/reload_config_entry',
                    entity_id = self.accumulated_consumption_current_hour
                )
            else:
                self.accumulated_unavailable += 1

            try:
                accumulated_kWh = self.last_accumulated_kWh
            except Exception as e:
                accumulated_kWh = round(float(runtime.minute/60) * (self.max_kwh_usage_pr_hour - self.buffer),2)
                self.log(f"Failed to get last accumulated kwh. Exception: {e}", level = 'WARNING')

            accumulated_kWh = round(self.last_accumulated_kWh + (current_consumption/60000),2)
            self.last_accumulated_kWh = accumulated_kWh
            self.accumulated_kWh_wasUnavailable = True

        else:
            if self.accumulated_kWh_wasUnavailable:
                # Log estimated during unavailable vs actual
                self.accumulated_kWh_wasUnavailable = False
                self.log(
                    f"Accumulated was unavailable. Estimated: {self.last_accumulated_kWh}. Actual: {accumulated_kWh}",
                    level = 'INFO'
                )
            accumulated_kWh = float(accumulated_kWh)
            self.last_accumulated_kWh = accumulated_kWh
            attr_last_updated = self.get_state(entity_id = self.accumulated_consumption_current_hour,
                attribute = "last_updated"
            )
            if not attr_last_updated:
                last_update: datetime = self.datetime(aware=True)
            else:
                last_update = self.convert_utc(attr_last_updated)

            now: datetime = self.datetime(aware=True)
            stale_time: timedelta = now - last_update
            if stale_time > datetime.timedelta(minutes = 2): # Stale for more than two minutes. Reload integration
                self.log(
                    f"Accumulated consumption has been stale for {stale_time} Reloading integration",
                    level = 'INFO'
                )
                self.call_service('homeassistant/reload_config_entry',
                    entity_id = self.accumulated_consumption_current_hour
                )

                if runtime.minute < 2:
                    accumulated_kWh = 1
                    self.last_accumulated_kWh = 1
                else:
                    accumulated_kWh += 0.5
                    self.last_accumulated_kWh += 0.5
                return


            # Check if production sensors exists and valid
        if self.current_production:
            current_production = self.get_state(self.current_production)
            if (
                current_production == 'unavailable'
                or current_production == 'unknown'
            ):
                current_production = 0
        else:
            current_production = 0
        if self.accumulated_production_current_hour:
            production_kWh = self.get_state(self.accumulated_production_current_hour)
            if (
                production_kWh == 'unavailable'
                or production_kWh == 'unknown'
            ):
                production_kWh = 0
        else:
            production_kWh = 0


            # Calculations used to adjust consumption
        max_target_kWh_buffer:float = round(((self.max_kwh_usage_pr_hour- self.buffer) * (runtime.minute/60)) - (accumulated_kWh - production_kWh) , 2)
        projected_kWh_usage:float = round((((current_consumption - current_production) /60000) * remaining_minute)  , 2)


            # Resets and logs every hour
        if runtime.minute == 0:
            self.last_accumulated_kWh = 0
            if (
                datetime.datetime.now().hour == 0
                and datetime.datetime.now().day == 1
            ):
                self.resetHighUsage()

            elif accumulated_kWh > self.top_usage_hour:
                self.logHighUsage()

            if (
                CHARGE_SCHEDULER.isPastChargingTime()
                or not CHARGE_SCHEDULER.isChargingTime()
            ):
                for c in self.chargers:
                    if (
                        c.Car.getLocation() == 'home'
                        and c.getChargingState() == 'Charging'
                    ):
                        if (
                            (c.priority == 1 or c.priority == 2)
                            and CHARGE_SCHEDULER.isPastChargingTime()
                        ):
                            pass

                        elif (
                            not c.dontStopMeNow()
                            and not self.SolarProducing_ChangeToZero
                        ):
                            c.stopCharging()
                            if CHARGE_SCHEDULER.isPastChargingTime():
                                self.log(
                                    f"Was not able to finish charging {c.charger} "
                                    f"with {c.kWhRemaining()} kWh remaining before prices increased.",
                                    level = 'INFO'
                                )

            for heater in reversed(self.heatersRedusedConsumption):
                heater.isOverconsumption = False
                self.heatersRedusedConsumption.remove(heater)


            """ Change consumption if above target or below production
            """

            # Current consuption is on it´s way to go over max kWh usage pr hour. Redusing usage
        elif (
            projected_kWh_usage + accumulated_kWh > self.max_kwh_usage_pr_hour - self.buffer
            or max_target_kWh_buffer < 0
        ):
            available_Wh:float = round((self.max_kwh_usage_pr_hour - self.buffer + (max_target_kWh_buffer * (60 / remaining_minute)))*1000 - (current_consumption) , 2)

            if (
                available_Wh > -800
                and remaining_minute > 15
                and not self.heatersRedusedConsumption
            ):
                return

            #if not self.queueChargingList:
            #    for c in self.chargers:
            #        if c.Car.getLocation() == 'home':
            #            c.Car.wakeMeUp()
            if available_Wh < -2000:
                self.findCharingNotInQueue()


            if self.queueChargingList:
                reduce_Wh, available_Wh = self.getHeatersReducedPreviousConsumption(available_Wh)

                if  reduce_Wh + available_Wh < 0 :
                    available_Wh = self.reduceChargingAmpere(available_Wh, reduce_Wh)


            for heater in self.heaters:
                if available_Wh < -100:
                    heater.prev_consumption = float(self.get_state(heater.consumptionSensor))
                    if (
                        heater.prev_consumption > 100
                        and heater not in self.heatersRedusedConsumption
                    ):
                        self.heatersRedusedConsumption.append(heater)
                        heater.setSaveState()
                        available_Wh += heater.prev_consumption
                else:
                    return


            # Reduce charging speed to turn heaters back on
        elif self.heatersRedusedConsumption:
            available_Wh:float = round((self.max_kwh_usage_pr_hour - self.buffer + (max_target_kWh_buffer * (60 / remaining_minute)))*1000 - (current_consumption) , 2)
            
            #for c in self.chargers:
            #    if c.Car.getLocation() == 'home':
            #        c.Car.wakeMeUp()

            reduce_Wh, available_Wh = self.getHeatersReducedPreviousConsumption(available_Wh)
 
            if (
                self.queueChargingList
                and reduce_Wh + available_Wh < 0
            ):
                available_Wh = self.reduceChargingAmpere(available_Wh, reduce_Wh)


            # Production is higher than consumption
        elif (
            accumulated_kWh <= production_kWh
            and projected_kWh_usage < 0
        ):
            """ If production is higher than consumption.
                TODO: Not tested properly

            """
            self.SolarProducing_ChangeToZero = True
            available_Wh:float = round(current_production - current_consumption , 2)

            # Check if any heater is reduced
            if self.heatersRedusedConsumption:
                for heater in reversed(self.heatersRedusedConsumption):
                    if heater.prev_consumption < available_Wh:
                        heater.setPreviousState()
                        available_Wh -= heater.prev_consumption
                        self.heatersRedusedConsumption.remove(heater)


            """ 
                TODO: If chargetime: Calculate if production is enough to charge wanted amount

            """
            
            if not self.solarChargingList :
                # Check if any is charging, or is not finished
                for c in self.chargers:
                    if c.Car.getLocation() == 'home':
                        if c.getChargingState() == 'Charging':
                            c.charging_on_solar = True
                            self.solarChargingList.append(c.charger_id)
                        elif (
                            c.getChargingState() == 'Stopped'
                            and c.Car.state_of_charge() < c.Car.pref_charge_limit
                            and available_Wh > 1600
                        ):
                            c.startCharging()
                            c.charging_on_solar = True
                            self.solarChargingList.append(c.charger_id)
                            AmpereToCharge = math.ceil(available_Wh / c.voltphase)
                            c.setChargingAmps(charging_amp_set = AmpereToCharge)
                            return

                # Check if any is below prefered charging limit
                for c in self.chargers:
                    if c.Car.getLocation() == 'home':
                        if c.getChargingState() == 'Charging':
                            self.solarChargingList.append(c.charger_id)
                            c.charging_on_solar = True
                        elif (
                            c.Car.pref_charge_limit > c.Car.oldChargeLimit
                        ):
                            c.charging_on_solar = True
                            c.Car.changeChargeLimit(c.Car.pref_charge_limit)
                            c.startCharging()
                            self.solarChargingList.append(c.charger_id)
                            AmpereToCharge = math.ceil(available_Wh / c.voltphase)
                            c.setChargingAmps(charging_amp_set = AmpereToCharge)
                            return

                pass
            else :
                for queue_id in self.solarChargingList:
                    for c in self.chargers:
                        if c.charger_id == queue_id:
                            if c.getChargingState() == 'Charging':
                                AmpereToIncrease = math.ceil(available_Wh / c.voltphase)
                                c.changeChargingAmps(charging_amp_change = AmpereToIncrease)
                                return
                            elif (
                                c.getChargingState() == 'Complete'
                                and c.Car.state_of_charge() >= c.Car.pref_charge_limit
                            ):
                                c.charging_on_solar = False
                                c.Car.changeChargeLimit(c.Car.oldChargeLimit)
                                try:
                                    self.solarChargingList.remove(queue_id)
                                except Exception as e:
                                    self.log(f"{c.charger} was not in solarChargingList. Exception: {e}", level = 'DEBUG')
                            elif c.getChargingState() == 'Complete':
                                c.charging_on_solar = False
                                try:
                                    self.solarChargingList.remove(queue_id)
                                except Exception as e:
                                    self.log(f"{c.charger} was not in solarChargingList. Exception: {e}", level = 'DEBUG')
                return


            # Set spend in heaters
            for heater in self.heaters:
                if (
                    float(self.get_state(heater.consumptionSensor)) < 100
                    and not heater.increase_now
                    and heater.normal_power < available_Wh
                ):
                    heater.setIncreaseState()
                    available_Wh -= heater.normal_power


            # Consumption is higher than production
        elif (
            (accumulated_kWh > production_kWh
            or projected_kWh_usage > 0)
            and self.SolarProducing_ChangeToZero
        ):
            """ If production is lower than consumption.
                TODO: Not tested properly

            """
            available_Wh:float = round(current_production - current_consumption , 2)

            # Remove spend in heaters
            for heater in self.heaters:
                if available_Wh > 0:
                    return

                if heater.increase_now:
                    heater.setPreviousState()
                    available_Wh += heater.normal_power

            # Reduce any chargers/batteries
            for queue_id in reversed(self.solarChargingList):
                for c in self.chargers:
                    if c.charger_id == queue_id:

                        if c.ampereCharging == 0:
                            c.ampereCharging = math.floor(float(self.get_state(c.charging_amps)))

                        if c.ampereCharging > 6:
                            AmpereToReduce = math.floor(available_Wh / c.voltphase)
                            if (c.ampereCharging + AmpereToReduce) < 6:
                                c.setChargingAmps(charging_amp_set = 6)
                                available_Wh += (c.ampereCharging - 6) * c.voltphase
                                # TODO: Check if remaining available is lower than production and stop charing.
                            else:
                                c.changeChargingAmps(charging_amp_change = AmpereToReduce)
                                available_Wh += AmpereToReduce * c.voltphase
                                break

            if current_production < 1000:
                """ 
                    Find proper idle consumption...
                    If production is low -> stop and reset.

                """
                self.SolarProducing_ChangeToZero = False
                for queue_id in reversed(self.solarChargingList):
                    for c in self.chargers:
                        if c.charger_id == queue_id:
                            c.charging_on_solar = False
                            c.Car.changeChargeLimit(c.Car.oldChargeLimit)
                            try:
                                self.solarChargingList.remove(queue_id)
                            except Exception as e:
                                self.log(f"{c.charger} was not in solarChargingList. Exception: {e}", level = 'DEBUG')


            # Increase charging speed or add another charger if time to charge
        elif (
            projected_kWh_usage + accumulated_kWh < self.max_kwh_usage_pr_hour - self.buffer
            and max_target_kWh_buffer > 0
        ):
            available_Wh:float = round((self.max_kwh_usage_pr_hour - self.buffer + (max_target_kWh_buffer * (60 / remaining_minute)))*1000 - (current_consumption) , 2)

            if (
                (remaining_minute > 10 and available_Wh < 800)
                or max_target_kWh_buffer < 0.1
            ):
                    return

            if self.findCharingNotInQueue():
                charger_id = None
                
                if self.queueChargingList:

                    for queue_id in self.queueChargingList:
                        for c in self.chargers:
                            if c.charger_id == queue_id:
                                if (
                                    c.getChargingState() == 'Complete'
                                    or c.getChargingState() == 'Disconnected'
                                ):
                                    try:
                                        self.queueChargingList.remove(queue_id)
                                        if (
                                            not self.queueChargingList
                                            and self.now_is_between('23:00:00', '06:00:00')
                                        ):
                                            self.logIdleConsumption()
                                    except Exception as e:
                                        self.log(f"{c.charger} was not in queueChargingList. Exception: {e}", level = 'DEBUG')

                                if (
                                    not CHARGE_SCHEDULER.isChargingTime()
                                    and c.getChargingState() == 'Stopped'
                                ):
                                    try:
                                        self.queueChargingList.remove(queue_id)
                                    except Exception as e:
                                        self.log(f"Was not able to remove {c.charger} from queueChargingList. Exception: {e}", level = 'DEBUG')

                                if (
                                    c.getChargingState() == 'Charging'
                                    and c.isChargingAtMaxAmps()
                                ):
                                    if  CHARGE_SCHEDULER.isChargingTime():
                                        if (
                                            len(CHARGE_SCHEDULER.chargingQueue) > len(self.queueChargingList)
                                            and available_Wh > 1600 and remaining_minute > 11
                                        ):
                                            charger_id = CHARGE_SCHEDULER.findChargerToStart()
                                            if c.charger_id == charger_id:
                                                charger_id = CHARGE_SCHEDULER.findNextChargerToStart()

                                elif c.getChargingState() == 'Charging':
                                    if (
                                        c.dontStopMeNow()
                                        or CHARGE_SCHEDULER.isChargingTime()
                                    ):
                                        AmpereToIncrease = math.floor(available_Wh / c.voltphase)
                                        c.changeChargingAmps(charging_amp_change = AmpereToIncrease)
                                        return


                if not self.queueChargingList:
                    if (
                        CHARGE_SCHEDULER.isChargingTime()
                        and available_Wh > 1600 and remaining_minute > 11
                    ):
                        if charger_id == None:
                            charger_id = CHARGE_SCHEDULER.findChargerToStart()

                if charger_id != None:
                    for c in self.chargers:
                        if c.charger_id == charger_id:
                            if c.charger_id not in self.queueChargingList:
                                c.startCharging()
                                self.queueChargingList.append(c.charger_id)
                                AmpereToCharge = math.floor(available_Wh / c.voltphase)
                                c.setChargingAmps(charging_amp_set = AmpereToCharge)
                                return



    def reduceChargingAmpere(self, available_Wh: float, reduce_Wh: float) -> float:
        """ Reduces charging to stay within max kWh.
        """
        reduce_Wh += available_Wh

        for queue_id in reversed(self.queueChargingList):
            for c in self.chargers:
                if (
                    c.charger_id == queue_id
                    and reduce_Wh < 0
                ):

                    if c.ampereCharging == 0:
                        c.ampereCharging = math.ceil(float(self.get_state(c.charging_amps)))

                    if c.ampereCharging > 6:
                        AmpereToReduce = math.floor(reduce_Wh / c.voltphase)
                        if (c.ampereCharging + AmpereToReduce) < 6:
                            c.setChargingAmps(charging_amp_set = 6)
                            available_Wh -= (c.ampereCharging  - 6) * c.voltphase
                            reduce_Wh -= (c.ampereCharging  - 6) * c.voltphase
                        else:
                            c.changeChargingAmps(charging_amp_change = AmpereToReduce)
                            available_Wh -= AmpereToReduce * c.voltphase
                            reduce_Wh -= AmpereToReduce * c.voltphase
                            break
        return available_Wh


        # Finds charger not started from queue.
    def findCharingNotInQueue(self) -> bool:
        softwareUpdates = False
        for c in self.chargers:
            if c.Car.getLocation() == 'home':
                if c.Car.SoftwareUpdates():
                    softwareUpdates = True
        # Stop other chargers if a car is updating software. Not able to adjust chargespeed when updating.
        if softwareUpdates:
            for c in self.chargers:
                if (
                    c.Car.getLocation() == 'home'
                    and not c.dontStopMeNow()
                    and c.getChargingState() == 'Charging'
                ):
                    c.stopCharging()
            return False

        for c in self.chargers:
            if (
                c.Car.getLocation() == 'home'
                and c.getChargingState() == 'Charging'
                and c.charger_id not in self.queueChargingList
                and not self.SolarProducing_ChangeToZero
            ):
                self.queueChargingList.append(c.charger_id)
        return True



    def getHeatersReducedPreviousConsumption(self, available_Wh:float) -> float:
        """ Function that finds the value of power consumption when heating for items that are turned down
            and turns the heating back on if there is enough available Watt
            or return what Watt to reduce charing to turn heating back on.
        """
        self.findCharingNotInQueue()
        reduce_Wh: float = 0

        for heater in reversed(self.heatersRedusedConsumption):
            if heater.prev_consumption < available_Wh:
                heater.setPreviousState()
                available_Wh -= heater.prev_consumption
                self.heatersRedusedConsumption.remove(heater)
            else:
                reduce_Wh -= heater.prev_consumption
        return reduce_Wh, available_Wh



    def findConsumptionAfterTurnedBackOn(self, kwargs) -> None:
        """ Functions to calculate and log consumption based on outside temperature
            to better be able to calculate chargingtime based on max kW pr hour usage
        """
        global ELECTRICITYPRICE
        for heater in self.heaters:
            heater.off_for_hours, turnsBackOn = ELECTRICITYPRICE.continuousHoursOff(peak_hours = heater.time_to_save)
            for daytime in heater.daytime_savings:
                if 'start' in daytime and 'stop' in daytime:
                    if not 'presence' in daytime:
                        off_hours = self.parse_datetime(daytime['stop']) - self.parse_datetime(daytime['start'])
                        if off_hours < datetime.timedelta(minutes = 0):
                            off_hours += datetime.timedelta(days = 1)

                        hoursOffInt = off_hours.seconds//3600
                        if heater.off_for_hours < hoursOffInt:
                            heater.off_for_hours = hoursOffInt
                            turnsBackOn = self.parse_datetime(daytime['stop'])
            if datetime.datetime.now().hour < turnsBackOn.hour:
                if heater.findConsumptionAfterTurnedOn_Handler != None:
                    if self.timer_running(heater.findConsumptionAfterTurnedOn_Handler):
                        try:
                            self.cancel_timer(heater.findConsumptionAfterTurnedOn_Handler)
                        except Exception as e:
                            self.log(
                                f"Was not able to stop existing handler to findConsumptionAfterTurnedBackOn for {heater.heater}. {e}",
                                level = "DEBUG"
                            )
                heater.findConsumptionAfterTurnedOn_Handler = self.run_at(heater.findConsumptionAfterTurnedOn, turnsBackOn)


    def calculateIdleConsumption(self, kwargs) -> None:
        global JSON_PATH
        global ELECTRICITYPRICE
        global OUT_TEMP
        global CHARGE_SCHEDULER

        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        available_Wh_toCharge:list = [float(ElectricityData['MaxUsage']['max_kwh_usage_pr_hour'])*1000] * 48
        turnsBackOn:int = 0

        for heaterName in ElectricityData['consumption']:
            if ElectricityData['consumption'][heaterName]['ConsumptionData']:
                out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
                    # Find closest temp registered with data
                if not out_temp_str in ElectricityData['consumption'][heaterName]['ConsumptionData']:
                    temp_diff:int = 0
                    closest_temp:int
                    for temps in ElectricityData['consumption'][heaterName]['ConsumptionData']:
                        if OUT_TEMP > float(temps):
                            if temp_diff != 0:
                                if temp_diff < OUT_TEMP - float(temps):
                                    continue
                            temp_diff = OUT_TEMP - float(temps)
                            closest_temp = temps
                        else:
                            if temp_diff != 0:
                                if temp_diff < float(temps) - OUT_TEMP:
                                    continue
                            temp_diff = float(temps) - OUT_TEMP
                            closest_temp = temps
                    out_temp_str = closest_temp

                if heaterName == 'idleConsumption':
                    for watt in range(len(available_Wh_toCharge)):
                        reducewatt = available_Wh_toCharge[watt]
                        reducewatt -= float(ElectricityData['consumption'][heaterName]['ConsumptionData'][out_temp_str]['Consumption'])
                        reducewatt -= float(ElectricityData['consumption'][heaterName]['ConsumptionData'][out_temp_str]['HeaterConsumption'])
                        available_Wh_toCharge[watt] = reducewatt
                else:
                    for heater in self.heaters:
                        if heaterName == heater.heater:
                            off_hours:int = 0
                            max_off_hours:int = 0
                            turn_on_at:int = 0
                            for t in heater.time_to_save:
                                if (
                                    not t - datetime.timedelta(hours = 1) in heater.time_to_save
                                    and t in heater.time_to_save
                                ):
                                    off_hours = 1
                                elif (
                                    t in heater.time_to_save
                                    and not t + datetime.timedelta(hours = 1) in heater.time_to_save
                                ):
                                    off_hours += 1
                                    if max_off_hours < off_hours:
                                        max_off_hours = off_hours
                                        turn_on_at = t.hour + 1
                                elif datetime.datetime.today().day == t.day:
                                    off_hours += 1
                                else:
                                    break
                                if turn_on_at < off_hours:
                                    turn_on_at += 24
                            if turnsBackOn < turn_on_at:
                                turnsBackOn = turn_on_at

                            if max_off_hours > 0:
                                max_off_hours = str(max_off_hours)
                                off_for = '0'
                                    # Find closest time registered with data
                                if not max_off_hours in ElectricityData['consumption'][heaterName]['ConsumptionData'][out_temp_str]:
                                    time_diff:int = 0
                                    closest_time:int
                                    for temps in ElectricityData['consumption'][heaterName]['ConsumptionData'][out_temp_str]:
                                        if OUT_TEMP > float(temps):
                                            if time_diff != 0:
                                                if time_diff < OUT_TEMP - float(temps):
                                                    continue
                                            time_diff = OUT_TEMP - float(temps)
                                            closest_time = temps
                                        else:
                                            if time_diff != 0:
                                                if time_diff < float(temps) - OUT_TEMP:
                                                    continue
                                            time_diff = float(temps) - OUT_TEMP
                                            closest_time = temps
                                    off_for = str(closest_time)
                                else:
                                    off_for = str(max_off_hours)

                                expectedHeaterConsumption = round(float(ElectricityData['consumption'][heaterName]['ConsumptionData'][out_temp_str][off_for]['Consumption']) * 1000, 2)
                                heaterWatt = ElectricityData['consumption'][heaterName]['power']
                                
                                while (
                                    turn_on_at < len(available_Wh_toCharge)
                                    and expectedHeaterConsumption > heaterWatt
                                ):
                                    watt = available_Wh_toCharge[turn_on_at]
                                    watt -= heaterWatt
                                    available_Wh_toCharge[turn_on_at] = watt
                                    expectedHeaterConsumption -= heaterWatt
                                    turn_on_at += 1
                                if expectedHeaterConsumption > 0:
                                    watt = available_Wh_toCharge[turn_on_at]
                                    watt -= expectedHeaterConsumption
                                    available_Wh_toCharge[turn_on_at] = watt

        CHARGE_SCHEDULER.turnsBackOn = turnsBackOn
        CHARGE_SCHEDULER.availableWatt = available_Wh_toCharge


    def logIdleConsumption(self) -> None:
        global JSON_PATH
        global OUT_TEMP

        current_consumption = float(self.get_state(self.current_consumption))
        heater_consumption:float = 0.0
        for heater in self.heaters:
            heater_consumption += float(self.get_state(heater.consumptionSensor))
        idle_consumption = current_consumption - heater_consumption
        if idle_consumption > 10:
            with open(JSON_PATH, 'r') as json_read:
                ElectricityData = json.load(json_read)

            consumptionData = ElectricityData['consumption']['idleConsumption']['ConsumptionData']
            out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)

            if not out_temp_str in ElectricityData['consumption']['idleConsumption']['ConsumptionData']:
                newData = {"Consumption" : round(idle_consumption,2),"HeaterConsumption" : round(heater_consumption,2), "Counter" : 1}
                ElectricityData['consumption']['idleConsumption']['ConsumptionData'].update({out_temp_str : newData})
            else:
                consumptionData = ElectricityData['consumption']['idleConsumption']['ConsumptionData'][out_temp_str]
                counter = consumptionData['Counter'] + 1
                if counter > 100:
                    return
                avgConsumption = round(((consumptionData['Consumption'] * consumptionData['Counter']) + idle_consumption) / counter,2)
                avgHeaterConsumption = round(((consumptionData['HeaterConsumption'] * consumptionData['Counter']) + heater_consumption) / counter,2)
                newData = {"Consumption" : avgConsumption, "HeaterConsumption" : avgHeaterConsumption, "Counter" : counter}
                ElectricityData['consumption']['idleConsumption']['ConsumptionData'].update({out_temp_str : newData})

            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)


        # Top three max kWh usage pr hour logging
    def logHighUsage(self) -> None:
        global JSON_PATH
        newTotal = 0.0
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        max_kwh_usage_top = ElectricityData['MaxUsage']['topUsage']

        try:
            newTopUsage = float(self.get_state(self.accumulated_consumption_current_hour))
            if newTopUsage > max_kwh_usage_top[0]:
                max_kwh_usage_top[0] = newTopUsage
                ElectricityData['MaxUsage']['topUsage'] = sorted(max_kwh_usage_top)
            self.top_usage_hour = ElectricityData['MaxUsage']['topUsage'][0]
        except ValueError as ve:
            self.log(
                f"Not able to set new Top Hour Usage. Accumulated consumption is {self.get_state(self.accumulated_consumption_current_hour)} "
                f"ValueError: {ve}",
                level = 'WARNING'
            )
        except Exception as e:
            self.log(f"Not able to set new Top Hour Usage. Exception: {e}", level = 'WARNING')

        for num in ElectricityData['MaxUsage']['topUsage']:
            newTotal += num
        avg_top_usage = newTotal / 3

        if avg_top_usage > self.max_kwh_usage_pr_hour:
            self.max_kwh_usage_pr_hour += 5
            ElectricityData['MaxUsage']['max_kwh_usage_pr_hour'] = self.max_kwh_usage_pr_hour 
            self.log(
                f"Avg consumption during one hour is now {round(avg_top_usage, 3)} kWh and surpassed max kWh set. "
                f"New max kWh usage during one hour set to {self.max_kwh_usage_pr_hour}. "
                "If this is not expected try to increase buffer.",
                level = 'WARNING'
            )
        elif avg_top_usage > self.max_kwh_usage_pr_hour - self.buffer:
            self.log(
                f"Consumption last hour: {round(newTopUsage, 3)}. "
                f"Avg top 3 hours: {round(avg_top_usage, 3)}",
                level = 'INFO'
            )

        with open(JSON_PATH, 'w') as json_write:
            json.dump(ElectricityData, json_write, indent = 4)

        # Resets max usage for new month
    def resetHighUsage(self) -> None:
        global JSON_PATH
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        self.max_kwh_usage_pr_hour = self.max_kwh_goal
        ElectricityData['MaxUsage']['max_kwh_usage_pr_hour'] = self.max_kwh_usage_pr_hour
        ElectricityData['MaxUsage']['topUsage'] = [0,0,float(self.get_state(self.accumulated_consumption_current_hour))]

        with open(JSON_PATH, 'w') as json_write:
            json.dump(ElectricityData, json_write, indent = 4)



        # Weather handling
    def outsideTemperatureUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global OUT_TEMP
        try:
            OUT_TEMP = float(new)
        except ValueError as ve:
            self.log(f"Not able to set new outdoor temperature: {new}. {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Not able to set new outdoor temperature: {new}. {e}", level = 'INFO')

    def outsideBackupTemperatureUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global OUT_TEMP
        try:
            if self.outside_temperature:
                # Check if main outside temperature sensor is back online
                if (
                    self.get_state(entity_id = self.outside_temperature) != 'unknown'
                    and self.get_state(entity_id = self.outside_temperature) != 'unavailable'
                ):
                    return
                # Else set temp from backup sensor and log
                self.log(
                    f"Outside Temperature is {self.get_state(entity_id = self.outside_temperature)}. "
                    f"Using backup from {self.weather_temperature}: {new}, old temp: {old}",
                    level = 'INFO'
                )
            OUT_TEMP = float(new)
        except ValueError as ve:
            self.log(
                f"Not able to set new outdoor temperaturefrom backup temperature / {self.weather_temperature}: {new}. "
                f"ValueError: {ve}",
                level = 'DEBUG'
            )
        except Exception as e:
            self.log(
                f"Not able to set new outdoor temperature from backup temperature / {self.weather_temperature}: {new}. "
                f"Exception: {e}",
                level = 'INFO'
            )

    def rainSensorUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global RAIN_AMOUNT
        try:
            RAIN_AMOUNT = float(new)
        except ValueError as ve:
            self.log(f"Not able to set new rain amount {new} ValueError: {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Not able to set new rain amount {new} Exception: {e}", level = 'INFO')

    def anemometerUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global WIND_AMOUNT
        try:
            WIND_AMOUNT = float(new)
        except ValueError as ve:
            self.log(f"Not able to set new wind amount {new} ValueError: {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Not able to set new wind amount {new} Exeption: {e}", level = 'INFO')


    def mode_event(self, event_name, data, kwargs) -> None:
        """ Listens to same mode event that I have used in Lightwand: https://github.com/Pythm/ad-Lightwand
            If mode name equals 'fire' it will turn off all charging and heating.
            To call from another app use: self.fire_event("MODE_CHANGE", mode = 'fire')
        """
        if data['mode'] == 'fire':
            for c in self.chargers:
                if (
                    c.Car.getLocation() == 'home'
                    and c.getChargingState() == 'Charging'
                ):
                    c.stopCharging()

            for heater in self.heaters:
                self.turn_off(heater.heater)

        if data['mode'] == 'away':
            if self.get_state(self.away_state) == 'off':
                pass
                #TODO: Any changes to consumption/heating when away?


class Scheduler:
    """ Class for calculating and schedule charge times
    """

    def __init__(self, api,
        informEveryChange:bool,
        stopAtPriceIncrease:float,
        startBeforePrice:float,
        infotext
    ):
        self.ADapi = api
        self.stopAtPriceIncrease = stopAtPriceIncrease
        self.startBeforePrice = startBeforePrice

        # Helpers
        self.chargingQueue:list = []
        self.chargingStart = None
        self.chargingStop = None
        self.price:float = 0.0
        self.informedStart = None
        self.informedStop = None
        self.informEveryChange:bool = informEveryChange
        self.informHandler = None
        self.infotext = infotext
       
        # Is updated from main class when turning off/down to save on electricity price
        self.turnsBackOn:int = 22
        self.availableWatt:list = []


    def calculateChargingTimes(self, kWhRemaining:float, totalW_AllChargers:float) -> int:
        """ Calculates expected charging time based on available power.
            Takes into consideration max kWh usage and logged usage based on outside temperature.
        """
        if self.availableWatt:
            h = self.turnsBackOn
            hoursToCharge = 0
            WhRemaining = kWhRemaining * 1000
            while (
                h < len(self.availableWatt)
                and WhRemaining > 0
            ):
                h += 1
                hoursToCharge += 1
                if self.availableWatt[h] < totalW_AllChargers:
                    WhRemaining -= self.availableWatt[h]
                else:
                    WhRemaining -= totalW_AllChargers
            return hoursToCharge

        self.ADapi.log(
            f"Calculating chargetime based on max Ampere charging. Expected available power not set",
            level = 'INFO'
        )
        return math.ceil(kWhRemaining / (totalW_AllChargers / 1000))


        """ Helpers used to return data
        """
    def isChargingTime(self) -> bool:
        global ELECTRICITYPRICE
        if (
            self.chargingStart != None
            and self.chargingStop != None
        ):
            if (
                datetime.datetime.today() >= self.chargingStart
                and datetime.datetime.today() < self.chargingStop
            ):
                return True
        return ELECTRICITYPRICE.elpricestoday[datetime.datetime.today().hour] <= self.price

    def isPastChargingTime(self) -> bool:
        if self.chargingStop == None:
            return True
        elif datetime.datetime.today() > self.chargingStop:
            return True
        return False

    def hasChargingScheduled(self, charger_id:str) -> bool:
        for c in self.chargingQueue:
            if charger_id == c['charger_id']:
                return True
        return False

    def findChargerToStart(self) -> str:
        if self.isChargingTime():
            pri = 1
            while pri < 5:
                for c in self.chargingQueue:
                    if c['priority'] == pri:
                        return c['charger_id']
                pri += 1
        return None

    def findNextChargerToStart(self) -> str:
        if self.isChargingTime():
            foundFirst = False
            pri = 1
            while pri < 5:
                for c in self.chargingQueue:
                    if c['priority'] == pri:
                        if not foundFirst:
                            foundFirst = True
                        else:
                            return c['charger_id']
                pri += 1
        return None


    def removeFromQueue(self, charger_id:str) -> None:
        """ Removes a charger from queue after finished charging or disconnected.
        """
        for c in self.chargingQueue:
            if charger_id == c['charger_id']:
                self.chargingQueue.remove(c)
        if len(self.chargingQueue) == 0:
            self.chargingStart = None
            self.chargingStop = None
            self.informedStart = None
            self.informedStop = None


    def queueForCharging(self,
        charger_id:str,
        kWhRemaining:float,
        maxAmps:int,
        voltphase:int,
        finishByHour:int,
        priority:int
    ) -> bool:
        """ Adds charger to queue and sets charging time
        """
        global RECIPIENTS
        global ELECTRICITYPRICE

        if kWhRemaining <= 0:
            self.removeFromQueue(charger_id = charger_id)
            return False

        if self.hasChargingScheduled(charger_id):
            for c in self.chargingQueue:
                if charger_id == c['charger_id']:
                    if (
                        c['kWhRemaining'] == kWhRemaining
                        and c['finishByHour'] == finishByHour
                    ):
                        return self.isChargingTime()
                    else:
                        c['kWhRemaining'] = kWhRemaining
                        c['finishByHour'] = finishByHour
        else:
            self.chargingQueue.append({'charger_id' : charger_id,
                'kWhRemaining' : kWhRemaining,
                'maxAmps' : maxAmps,
                'voltphase' : voltphase,
                'finishByHour' : finishByHour,
                'priority' : priority})


        self.chargingStart = None
        self.chargingStop = None
        kWhToCharge:float = 0.0
        totalW_AllChargers:float = 0.0
        finishByHour:int = 48
        pri:int = 0

        def by_value(item):
            return item['finishByHour']
        for c in sorted(self.chargingQueue, key=by_value):
            kWhToCharge += c['kWhRemaining']
            totalW_AllChargers += c['maxAmps'] * c['voltphase']

            if finishByHour == 48:
                finishByHour = c['finishByHour']
                pri = c['priority']
            elif pri < c['priority']:
                estHourCharge = 0
                try:
                    estHourCharge = c['kWhRemaining'] / (self.availableWatt[finishByHour] / 1000 )
                except (ValueError, TypeError):
                    estHourCharge = c['kWhRemaining'] / ((c['maxAmps'] * c['voltphase'])/1000)
                    self.ADapi.log(
                        f"{c['charger_id']} Could not get availableWatt. Using maxAmp * voltage = {estHourCharge} estimated hours charge",
                        level = 'INFO'
                    )
                except Exception as e:
                    self.ADapi.log(f"{c['charger_id']} Could not get availableWatt. Exception: {e}", level = 'WARNING')

                if c['finishByHour'] - finishByHour > estHourCharge:
                    finishByHour += math.floor(estHourCharge)
                else:
                    finishByHour = c['finishByHour']
                pri = c['priority']
            # TODO: Revisit logic

        hoursToCharge = self.calculateChargingTimes(
            kWhRemaining = kWhToCharge,
            totalW_AllChargers = totalW_AllChargers
        )

        if (
            self.ADapi.now_is_between('07:00:00', '14:00:00')
            and len(ELECTRICITYPRICE.elpricestoday) == 24
        ):
            # Finds low price during day awaiting tomorrows prices
            """ TODO: A better logic to charge if price is lower than usual before tomorrow prices is available from Nordpool.
            for c in self.chargingQueue:
                kWhToCharge += c['kWhRemaining']
            hoursToCharge = self.calculateChargingTimes(kWhRemaining = kWhToCharge, totalW_AllChargers = totalW_AllChargers)
            
            Check against hours until 14:00 and use smallest value hour to find lowest price to charge
            """
            self.price = ELECTRICITYPRICE.sorted_elprices_today[hoursToCharge]
            self.ADapi.log(
                f"Wait for tomorrows prices before setting chargetime for {charger_id}. "
                f"Charge if price is lower than {ELECTRICITYPRICE.currency} {self.price} (incl tax)",
                level = 'INFO'
            )
            return self.isChargingTime()

        self.chargingStart, self.chargingStop, self.price = ELECTRICITYPRICE.getContinuousCheapestTime(
            hoursTotal = hoursToCharge,
            calculateBeforeNextDayPrices = False,
            startTime = datetime.datetime.today().hour,
            finishByHour = finishByHour
        )

        return self.isChargingTime()


    def sumAndInformChargetime(self, kwargs) -> None:
        if (
            self.chargingStart != None
            and self.chargingStop != None
        ):
            self.ADapi.log(
                f"Start charge at {self.chargingStart}. Finished at {self.chargingStop}.",
                level = 'INFO'
            )
            charging_Start, charging_Stop = self.wideningChargingTime(
                ChargingAt = self.chargingStart,
                EndAt = self.chargingStop,
                price = self.price
            )
            self.chargingStart = charging_Start

            if self.infotext:
                infotxt:str = f"Start charge at {self.chargingStart}. Finish estimated at {self.chargingStop}. Stop no later than {charging_Stop}"
                self.ADapi.set_state(self.infotext,
                    state = infotxt
                )

            if (
                self.chargingStart != self.informedStart
                or self.chargingStop != self.informedStop
                or self.informEveryChange
            ):
                for r in RECIPIENTS:
                    self.ADapi.notify(
                        f"Start charge at {self.chargingStart}. Finished at {self.chargingStop}",
                        title = "🔋 Charge Queue",
                        name = r
                    )
            self.informedStart = self.chargingStart
            self.informedStop = self.chargingStop
            self.chargingStop = charging_Stop
            
            self.ADapi.log(
                f"chargingStart after widening: {self.chargingStart}. chargingStop {self.chargingStop}.",
                level = 'INFO'
            )


    def wideningChargingTime(self, ChargingAt, EndAt, price) -> datetime:
        global ELECTRICITYPRICE
        EndChargingHour = EndAt.hour
        if EndAt.day - 1 == datetime.datetime.today().day:
            EndChargingHour += 24

        # Check when charging needs to stop be cause of price increase
        while (
            EndChargingHour < len(ELECTRICITYPRICE.elpricestoday) -1
            and price + self.stopAtPriceIncrease > ELECTRICITYPRICE.elpricestoday[EndChargingHour]
        ):
            EndChargingHour += 1
            EndAt += datetime.timedelta(hours = 1)

        EndAt = EndAt.replace(minute = 0, second = 0, microsecond = 0)

        StartChargingHour = ChargingAt.hour
        if ChargingAt.day - 1 == datetime.datetime.today().day:
            StartChargingHour += 24
        startHourPrice = ELECTRICITYPRICE.elpricestoday[StartChargingHour]

        # Check if charging should be postponed one hour or start earlier
        if (
            price < startHourPrice - (self.stopAtPriceIncrease * 1.5)
            and startHourPrice < ELECTRICITYPRICE.elpricestoday[StartChargingHour+1] - (self.stopAtPriceIncrease * 1.3)
        ):
            ChargingAt += datetime.timedelta(hours = 1)
        else:
            hoursToChargeStart = ChargingAt - datetime.datetime.today().replace(second = 0, microsecond = 0)
            hoursToStart = hoursToChargeStart.seconds//3600

            while (
                hoursToStart > 0
                and startHourPrice + self.startBeforePrice >= ELECTRICITYPRICE.elpricestoday[StartChargingHour-1]
                and price + (self.startBeforePrice * 2) >= ELECTRICITYPRICE.elpricestoday[StartChargingHour-1]
            ):
                StartChargingHour -= 1
                hoursToStart -= 1
                ChargingAt -= datetime.timedelta(hours = 1)

        return ChargingAt, EndAt


class Charger:
    """ Charger
        Parent class for chargers

        Functions not returning valid data in parent:
        - def getChargingState(self) -> str:

        Functions need to finish call in child:
        - def startCharging(self) -> bool:
        - def stopCharging(self) -> bool:
        - def checkIfChargingStarted(self, kwargs) -> bool:
        - def checkIfChargingStopped(self, kwargs) -> bool:

    """

    def __init__(self, api,
        Car, # Car connecting to charger
        namespace,
        charger, # Name of your car. Mostly used for logging
        charger_id, # ID used to make API calls
        charger_sensor, # Cable Connected or Disconnected
        charger_switch, # Charging or not
        charging_amps, # Ampere charging
        charger_power, # Charger power
        session_energy, # Charged this session in kWh
        volts:int,
        phases:int,
        priority:int, # Priority. See full description in Readme
        finishByHour, # HA input_number for when car should be finished charging
        charge_now, # HA input_boolean to bypass smartcharging if true
        charge_on_solar, # HA input_boolean to charge only on solar
        departure, # HA input_datetime for when to have car finished charging to 100%. Not implemented yet
        guest # HA input_boolean for when guests borrows charger.
    ):

        self.ADapi = api
        self.Car = Car
        self.namespace = namespace
        self.charger = charger
        self.charger_id = charger_id

        self.charger_sensor = charger_sensor
        self.charger_switch = charger_switch
        self.charging_amps = charging_amps
        self.charger_power = charger_power
        self.session_energy = session_energy

        self.priority:int = priority
        if self.priority > 5:
            self.priority = 5


        if not finishByHour:
            self.finishByHour = 7
        else:
            if (
                not self.namespace
                or self.ADapi.get_state(finishByHour, namespace = self.namespace) == None
            ):
                self.finishByHour = math.ceil(float(self.ADapi.get_state(finishByHour)))
                self.ADapi.listen_state(self.finishByHourListen, finishByHour)
            else:
                self.finishByHour = math.ceil(float(self.ADapi.get_state(finishByHour,
                    namespace = self.namespace))
                )
                self.ADapi.listen_state(self.finishByHourListen, finishByHour,
                    namespace = self.namespace
                )

        if not charge_now:
            self.charge_now = False
        else:
            self.charge_now_HA = charge_now
            if (
                not self.namespace
                or self.ADapi.get_state(charge_now, namespace = self.namespace) == None
            ):
                self.charge_now = self.ADapi.get_state(charge_now)  == 'on'
                self.ADapi.listen_state(self.chargeNowListen, charge_now)
            else:
                self.charge_now = self.ADapi.get_state(charge_now, namespace = self.namespace)  == 'on'
                self.ADapi.listen_state(self.chargeNowListen, charge_now,
                    namespace = self.namespace
                )

        if not charge_on_solar:
            self.charge_on_solar = False
        else:
            if (
                not self.namespace
                or self.ADapi.get_state(charge_on_solar, namespace = self.namespace) == None
            ):
                self.charge_on_solar = self.ADapi.get_state(charge_on_solar)  == 'on'
                self.ADapi.listen_state(self.charge_on_solar_Listen, charge_on_solar)
            else:
                self.charge_on_solar = self.ADapi.get_state(charge_on_solar, namespace = self.namespace)  == 'on'
                self.ADapi.listen_state(self.charge_on_solar_Listen, charge_on_solar,
                    namespace = self.namespace
                )

        if not guest:
            self.guestCharging = False
        else:
            if (
                not self.namespace
                or self.ADapi.get_state(guest, namespace = self.namespace) == None
            ):
                self.guestCharging = self.ADapi.get_state(guest) == 'on'
                self.ADapi.listen_state(self.guestChargingListen, guest)
            else:
                self.guestCharging = self.ADapi.get_state(guest, namespace = self.namespace) == 'on'
                self.ADapi.listen_state(self.guestChargingListen, guest,
                    namespace = self.namespace
                )


            # Helpers
        self.ampereCharging:int = 0
        self.charging_on_solar:str = False
        self.voltPhase:int = 220
        self.checkCharging_handler = None

            # Set variables
        if self.Car.getLocation() == 'home' :
            #self.updateAmpereCharging()
            self.setVoltPhase(volts = volts, phases = phases)


        global JSON_PATH
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.charger_id in ElectricityData['charger']:
            
            ElectricityData['charger'].update(
                {self.charger_id : {"voltPhase" : self.voltPhase}}
            )

            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
        else:
            if 'voltPhase' in ElectricityData['charger'][self.charger_id]:
                self.voltphase = int(ElectricityData['charger'][self.charger_id]['voltPhase'])
            if 'MaxAmp' in ElectricityData['charger'][self.charger_id]:
                self.maxChargerAmpere = int(ElectricityData['charger'][self.charger_id]['MaxAmp'])

        if (
            self.session_energy
            and not self.guestCharging
        ):
            try:
                energy_charged = float(self.ADapi.get_state(self.session_energy))
            except ValueError:
                energy_charged = 0
            except Exception as e:
                energy_charged = 0
                self.ADapi.log(f"Error trying to get session energy from {self.session_energy}", level = 'DEBUG')
            if self.Car.maxkWhCharged < energy_charged:
                self.Car.maxkWhCharged = energy_charged
                ElectricityData['charger'][self.Car.vehicle_id].update(
                {"MaxkWhCharged" : energy_charged}
            )

            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)


        """ TODO Departure / Maxrange handling: To be re-written before implementation
            Set a departure time in a HA datetime sensor for when car will be finished charging to 100%,
            to have a optimal battery when departing.
        """
        self.max_range_handler = None
        self.start_charging_max = None
        if departure != None:
            self.departure = departure
    #    else:
    #        self.departure = 'input_datetime.departure_time_max_range'
    #        if not self.ADapi.entity_exists(self.ADapi.get_entity(self.departure)):
    #            self.ADapi.set_state(self.departure, state = self.ADapi.parse_time('00:00:00'))
    #        else:
    #            self.ADapi.log(f"'input_datetime.departure_time_max_range' configured for {self.charger} during setup. ")
        """
            Add Maxrange solution for charging finished to 100% at given time.
            #self.ADapi.listen_state(self.MaxRangeListener, self.departure, duration = 5 )
        """

        if self.Car.charge_limit:
            self.Car.oldChargeLimit = self.ADapi.get_state(self.Car.charge_limit)
            self.ADapi.listen_state(self.ChargeLimitChanged, self.Car.charge_limit)
        else:
            self.Car.oldChargeLimit = 100

        self.ADapi.run_in(self.findNewChargeTimeWhen, 80)


        """ End initialization Charger Class
        """


    def finishByHourListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listener for HA input number for when car should be finished charging
            Finds new time if changed
        """
        if not self.namespace:
            self.finishByHour = math.ceil(float(self.ADapi.get_state(entity)))
        else:
            self.finishByHour = math.ceil(float(self.ADapi.get_state(entity,
                namespace = self.namespace))
            )
        
        if not self.findNewChargeTime():
            self.stopCharging()


    def chargeNowListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listener for HA input boolean to disable smart charing and charge car now
            Starts charing if turn on, finds new chargetime if turned off
        """
        if not self.namespace:
            self.charge_now = self.ADapi.get_state(entity) == 'on'
        else:
            self.charge_now = self.ADapi.get_state(entity, namespace = self.namespace) == 'on'
        if (
            new == 'on'
            and old == 'off'
        ):
            self.startCharging()
        elif (
            new == 'off'
            and old == 'on'
        ):
            if not self.findNewChargeTime():
                self.stopCharging()


    def turnOff_Charge_now(self) -> None:
        if self.charge_now:
            if self.namespace:
                self.ADapi.set_state(self.charge_now_HA,
                    namespace = self.namespace,
                    state = 'off'
                )
            else:
                self.ADapi.set_state(self.charge_now_HA,
                    state = 'off'
                )


    def charge_on_solar_Listen(self, entity, attribute, old, new, kwargs) -> None:
        """ Listener for HA input boolean to enable/disable solar charing
        """
        global CHARGE_SCHEDULER
        if not self.namespace:
            self.charge_on_solar = self.ADapi.get_state(entity) == 'on'
        else:
            self.charge_on_solar = self.ADapi.get_state(entity, namespace = self.namespace) == 'on'
        if new == 'on':
            CHARGE_SCHEDULER.removeFromQueue(charger_id = self.charger_id)
            self.turnOff_Charge_now()
        elif new == 'off':
            if not self.findNewChargeTime():
                self.stopCharging()


    def guestChargingListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Disables logging and schedule if guest is using charger 
        """
        if not self.namespace:
            self.guestCharging = self.ADapi.get_state(entity) == 'on'
        else:
            self.guestCharging = self.ADapi.get_state(entity, namespace = self.namespace) == 'on'
        if (
            new == 'on'
            and old == 'off'
        ):
            self.startCharging()
        elif (
            new == 'off'
            and old == 'on'
        ):
            if not self.findNewChargeTime():
                self.stopCharging()


    def kWhRemaining(self) -> float:
        kWhRemain = self.Car.kWhRemaining()
        if kWhRemain == -2:
            status = self.ADapi.get_state(self.charger_sensor)
            if (
                status == 'completed'
                or status == 'disconnected'
            ):
                self.Car.kWhRemainToCharge = -1

            elif self.session_energy:
                if self.guestCharging:
                    return 100 - (float(self.ADapi.get_state(self.session_energy)))
                self.Car.kWhRemainToCharge = self.Car.maxkWhCharged - float(self.ADapi.get_state(self.session_energy))
        self.ADapi.log(f"kWhRemain to charge: {self.Car.kWhRemainToCharge} for {self.charger}")
        return self.Car.kWhRemainToCharge


        # Functions for charge times
    def findNewChargeTimeWhen(self, kwargs) -> None:
        """ Function to run when initialized and when new prices arrive.
        """
        if (
            self.Car.getLocation() == 'home'
            and self.kWhRemaining() > 0
        ):
            if self.Car.asleep():
                self.Car.wakeMeUp()
            if self.findNewChargeTime():
                if self.getChargingState() == 'Charging':
                    self.updateAmpereCharging()
                elif self.getChargingState() == 'Stopped':
                    self.startCharging()
            else:
                self.stopCharging()
        

    def findNewChargeTime(self) -> bool:
        global CHARGE_SCHEDULER

        if (
            self.Car.getLocation() == 'home'
            and self.getChargingState() != 'Disconnected'
            and self.getChargingState() != 'Complete'
            and not self.charging_on_solar
            and not self.charge_on_solar
        ):
            if CHARGE_SCHEDULER.informHandler != None:
                if self.ADapi.timer_running(CHARGE_SCHEDULER.informHandler):
                    try:
                        self.ADapi.cancel_timer(CHARGE_SCHEDULER.informHandler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not possible to stop timer to run sum and inform chargetime. Exception: {e}",
                            level = 'DEBUG'
                        )
            CHARGE_SCHEDULER.informHandler = self.ADapi.run_in(CHARGE_SCHEDULER.sumAndInformChargetime, 2)

            return CHARGE_SCHEDULER.queueForCharging(
                charger_id = self.charger_id,
                kWhRemaining = self.kWhRemaining(),
                maxAmps = self.getmaxChargingAmps(),
                voltphase = self.voltphase,
                finishByHour = self.finishByHour,
                priority = self.priority
            )
        return False


    def hasChargingScheduled(self) -> bool:
        global CHARGE_SCHEDULER
        return CHARGE_SCHEDULER.hasChargingScheduled(self.charger_id)


    def dontStopMeNow(self) -> bool:
         # Returns true if charger should not or can not be stopped
        if (
            self.charge_now
            or self.charging_on_solar
        ):
            return True
        return self.Car.SoftwareUpdates()


    def getChargingState(self) -> str:
        #Valid returns:
        #'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting'
        if (
            self.Car.getLocation() != 'home'
            or not self.isConnected()
        ):
            return 'Disconnected'
        if self.getChargerPower() >= 1:
            return 'Charging'
        elif self.kWhRemaining() <= 0:
            return 'Complete'
        else:
            return 'Stopped'
    

    def getChargerPower(self) -> int:
        # Returns power in kWh.
        pwr = self.ADapi.get_state(self.charger_power)
        try:
            pwr = float(pwr)
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {pwr} ValueError: {ve}",
                level = 'DEBUG'
            )
            pwr = 0
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {pwr} TypeError: {te}",
                level = 'WARNING'
            )
            pwr = 0
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {pwr} Exception: {e}",
                level = 'WARNING'
            )
            pwr = 0
        return pwr


    def getmaxChargingAmps(self) -> int:
        if self.guestCharging:
            return self.maxChargerAmpere
        return self.Car.car_limit_max_charging


    def isChargingAtMaxAmps(self) -> bool:
        if self.getmaxChargingAmps() <= self.ampereCharging:
            if (
                math.ceil(float(self.ADapi.get_state(self.charging_amps))) == self.ampereCharging
                or math.floor(float(self.ADapi.get_state(self.charging_amps))) == self.ampereCharging
            ):
                return True
        return False


    def updateAmpereCharging(self) -> None:
        if self.ADapi.get_state(self.charging_amps) != 'unavailable':
            self.ampereCharging = math.ceil(float(self.ADapi.get_state(self.charging_amps)))



    def changeChargingAmps(self, charging_amp_change:int = 0) -> None:
        """ Function to change ampere charging +/-
        """
        if charging_amp_change != 0:
            if self.ampereCharging == 0:
                self.updateAmpereCharging()
            new_charging_amp = self.ampereCharging + charging_amp_change
            self.setChargingAmps(charging_amp_set = new_charging_amp)


    def setChargingAmps(self, charging_amp_set:int = 16) -> int:
        """ Function to set ampere charging to received value
            returns actual restricted within min/max ampere
        """
        if charging_amp_set > self.getmaxChargingAmps():
            self.ampereCharging = self.getmaxChargingAmps()
        elif charging_amp_set < 6:
            self.ampereCharging = 6
        else:
            self.ampereCharging = charging_amp_set
        stack = inspect.stack() # Check if called from child
        if stack[1].function == 'setChargingAmps':
            return self.ampereCharging
        else:
            if self.namespace:
                self.ADapi.set_state(self.charging_amps,
                    namespace = self.namespace,
                    state = self.ampereCharging
                )
            else:
                self.ADapi.set_state(self.charging_amps,
                    state = self.ampereCharging
                )


    def ChargingConnected(self, entity, attribute, old, new, kwargs) -> None:
        self.ADapi.log(f"ChargingConnected not implemented in parent class for {self.charger}", level = 'WARNING')


    def ChargeLimitChanged(self, entity, attribute, old, new, kwargs) -> None:
        global CHARGE_SCHEDULER
        if self.Car.getLocation() == 'home':
            try:
                self.oldChargeLimit = int(old)
                new = int(new)
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.charger} new charge limit: {new}. Error: {ve}",
                    level = 'INFO' #'DEBUG'
                )
                return
            except Exception as e:
                self.ADapi.log(
                    f"Not able to process {self.charger} new charge limit: {new}. Exception: {e}",
                    level = 'WARNING'
                )
                return

            try:
                battery_state = float(self.ADapi.get_state(self.Car.battery_sensor))
            except (ValueError, TypeError) as ve:
                self.ADapi.log(
                    f"{self.charger} battery state error {battery_state} when setting new charge limit: {new}. Error: {ve}",
                    level = 'INFO' #'DEBUG'
                )
                return
            if battery_state > float(new):
                if self.hasChargingScheduled():
                    CHARGE_SCHEDULER.removeFromQueue(charger_id = self.charger_id)
                    self.turnOff_Charge_now()
                    self.kWhRemainToCharge = -1

            else:
                if not self.findNewChargeTime():
                    self.stopCharging()

    
    def startCharging(self) -> bool:
        state:str = self.getChargingState()
        if (
            self.kWhRemaining() > 0
            and state != 'Complete'
            and state != 'Disconnected'
        ):
            if (
                state != 'Charging'
                and state != 'Starting'
            ):
                if self.checkCharging_handler != None:
                    if self.ADapi.timer_running(self.checkCharging_handler):
                        try:
                            self.ADapi.cancel_timer(self.checkCharging_handler)
                        except Exception as e:
                            self.ADapi.log(
                                f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                                level = 'DEBUG'
                            )
                self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)

                stack = inspect.stack() # Check if called from child
                if stack[1].function == 'startCharging':
                    return True
                else:
                    if self.namespace:
                        self.ADapi.set_state(self.charger_switch,
                            namespace = self.namespace,
                            state = 'on'
                        )
                    else:
                        self.ADapi.set_state(self.charger_switch,
                            state = 'on'
                        )

        elif self.getChargingState() == 'Complete':
             CHARGE_SCHEDULER.removeFromQueue(charger_id = self.charger_id)
             self.turnOff_Charge_now()

        return False


    def stopCharging(self) -> bool:
        if (
            not self.dontStopMeNow()
            and self.getChargingState() == 'Charging'
        ):
            if self.checkCharging_handler != None:
                if self.ADapi.timer_running(self.checkCharging_handler):
                    try:
                        self.ADapi.cancel_timer(self.checkCharging_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                            level = 'DEBUG'
                        )
                    finally:
                        self.checkCharging_handler = None
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

            stack = inspect.stack() # Check if called from child
            if stack[1].function == 'stopCharging':
                return True
            else:
                if self.namespace:
                    self.ADapi.set_state(self.charger_switch,
                        namespace = self.namespace,
                        state = 'off'
                    )
                else:
                    self.ADapi.set_state(self.charger_switch,
                        state = 'off'
                    )
        return False


    def checkIfChargingStarted(self, kwargs) -> bool:
        if (
            self.getChargingState() != 'Charging'
            and self.getChargingState() != 'Complete'
        ):
            if self.ADapi.timer_running(self.checkCharging_handler):
                try:
                    self.ADapi.cancel_timer(self.checkCharging_handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                        level = 'DEBUG'
                    )
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)

            stack = inspect.stack() # Check if called from child
            if (
                stack[1].function == 'startCharging'
                or stack[1].function == 'checkIfChargingStarted'
            ):
                return False
            else:
                if self.namespace:
                    self.ADapi.set_state(self.charger_switch,
                        namespace = self.namespace,
                        state = 'on'
                    )
                else:
                    self.ADapi.set_state(self.charger_switch,
                        state = 'on'
                    )
        return True


    def checkIfChargingStopped(self, kwargs) -> bool:
        if self.dontStopMeNow():
            return True
        if self.getChargingState() == 'Charging':
            if self.ADapi.timer_running(self.checkCharging_handler):
                try:
                    self.ADapi.cancel_timer(self.checkCharging_handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                        level = 'DEBUG'
                    )
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

            stack = inspect.stack() # Check if called from child
            if (
                stack[1].function == 'stopCharging'
                or stack[1].function == 'checkIfChargingStopped'
            ):
                return False
            else:
                if self.namespace:
                    self.ADapi.set_state(self.charger_switch,
                        namespace = self.namespace,
                        state = 'off'
                    )
                else:
                    self.ADapi.set_state(self.charger_switch,
                        state = 'off'
                    )
        return True


    def ChargingStarted(self, entity, attribute, old, new, kwargs) -> None:
        global CHARGE_SCHEDULER
        if self.Car.getLocation() == 'home':
            if not self.hasChargingScheduled():
                if not self.findNewChargeTime():
                    self.stopCharging()

            elif CHARGE_SCHEDULER.chargingStart:
                if CHARGE_SCHEDULER.chargingStart - datetime.timedelta(minutes=12) < datetime.datetime.now():
                    pass

                elif not CHARGE_SCHEDULER.isChargingTime():
                    self.stopCharging()


    def ChargingStopped(self, entity, attribute, old, new, kwargs) -> None:
        global CHARGE_SCHEDULER
        global RECIPIENTS
        try:
            if (
                self.kWhRemaining() <= 2
                or CHARGE_SCHEDULER.isPastChargingTime()
            ):
                if self.getChargingState() == 'Complete':
                    CHARGE_SCHEDULER.removeFromQueue(charger_id = self.charger_id)
                    self.turnOff_Charge_now()

                    self.setChargingAmps(charging_amp_set = 6) # Set to 6 amp for preheat... CHECKME

            self.ampereCharging = 0

        except AttributeError as ae:
            self.ADapi.log(f"Attribute Error in ChargingStopped: {ae}", level = 'DEBUG')
        except Exception as e:
            self.ADapi.log(f"Exception in ChargingStopped: {e}", level = 'WARNING')


    def setVoltPhase(self, volts:int = 220, phases:int = 1) -> None:
        """ Helper for calculations on chargespeed.
            VoltPhase is a make up name and simplification to calculate chargetime based on remaining kwh to charge
            230v 1 phase,
            266v is 3 phase on 230v without neutral (supported by tesla among others)
            687v is 3 phase on 400v with neutral.
        """
        voltphase = 220

        if (
            phases == 3
            and volts > 200
            and volts < 250
        ):
            voltphase = 266

        elif (
            phases == 3
            and volts > 300
        ):
            voltphase = 687

        elif (
            phases == 1
            and volts > 200
            and volts < 250
        ):
            voltphase = volts

        global JSON_PATH
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if self.charger_id in ElectricityData['charger']:
            ChargerInfo = ElectricityData['charger'][self.charger_id]
            if (
                'voltPhase' in ChargerInfo
                and voltphase == 220
            ):
                self.voltphase = int(ChargerInfo['voltPhase'])
            else:
                self.voltphase = voltphase
                if int(ChargerInfo['voltPhase']) != voltphase:
                    ChargerInfo.update(
                        { "voltPhase" : voltphase}
                    )
                    ElectricityData['charger'][self.charger_id].update(ChargerInfo)
                    
                    with open(JSON_PATH, 'w') as json_write:
                        json.dump(ElectricityData, json_write, indent = 4)


class Car:
    """ Car
        Parent class for cars

        Variables to set in child before init:
        - self.vehicle_id:str

        Functions not returning valid data in parent:


        Functions need to finish call in child:


    """

    def __init__(self, api,
        namespace,
        carName,
        charger_sensor, # Sensor chargecable connected
        charge_limit, # SOC limit sensor in %
        battery_sensor, # SOC (State Of Charge) in %
        asleep_sensor, # If car is sleeping
        online_sensor, # If car is online
        location_tracker, # Location of car/charger
        destination_location_tracker, # Destination of car
        arrival_time, # Sensor with Arrival time, estimated energy at arrival and destination.
        software_update, # If cars updates software it probably can`t change charge speed or stop charging
        force_data_update, # Force Home Assistant to pull new data
        polling_switch, # Turn off Home Assistant pulling data from car
        data_last_update_time, # Last time Home Assistant pulled data
        battery_size:int, # Size of battery in kWh
        pref_charge_limit:int # Preferred chargelimit
    ):
        """ TODO:
            - Implement destination location tracker. If destination is Home then calculate charging based on arrival time.
        """

        self.ADapi = api
        self.namespace = namespace

        self.charger_sensor = charger_sensor
        self.charge_limit = charge_limit
        self.battery_sensor = battery_sensor

        self.asleep_sensor = asleep_sensor
        self.online_sensor = online_sensor
        self.location_tracker = location_tracker
        self.destination_location_tracker = destination_location_tracker
        self.arrival_time = arrival_time
        self.software_update = software_update
        self.force_data_update = force_data_update
        self.polling_switch = polling_switch
        self.data_last_update_time = data_last_update_time

        self.battery_size:int = battery_size
        self.pref_charge_limit:int = pref_charge_limit

        # Variables:
        self.car_limit_max_charging:int = 32 # Max ampere the car can receive
        self.maxkWhCharged:float = 5 # Max kWh car has charged
        if not hasattr(self, 'vehicle_id'):
            self.vehicle_id = carName
        self.carName = carName

        global JSON_PATH
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.vehicle_id in ElectricityData['charger']:
            ElectricityData['charger'].update(
                {self.vehicle_id : {"CarLimitAmpere" : 6, "MaxkWhCharged" : 5}}
            )
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
        else:
            if 'CarLimitAmpere' in ElectricityData['charger'][self.vehicle_id]:
                self.car_limit_max_charging = math.ceil(float(ElectricityData['charger'][self.vehicle_id]['CarLimitAmpere']))
            if 'MaxkWhCharged' in ElectricityData['charger'][self.vehicle_id]:
                self.maxkWhCharged = float(ElectricityData['charger'][self.vehicle_id]['MaxkWhCharged'])
            self.ADapi.log(f"Limit max set to {self.car_limit_max_charging} and max charged set to {self.maxkWhCharged} for {self.carName}")

        self.kWhRemainToCharge = -1
        self.kWhRemainToCharge = self.kWhRemaining()

        if self.charger_sensor:
            self.ADapi.listen_state(self.ChargeCableConnected, self.charger_sensor, new = 'on')
            self.ADapi.listen_state(self.ChargeCableDisconnected, self.charger_sensor, new = 'off')
        else:
            self.cableConnected = True
        self.cableConnected = self.isConnected()


        """ End initialization Car Class
        """


    def ChargeCableConnected(self, entity, attribute, old, new, kwargs) -> None:
        self.cableConnected = True


    def ChargeCableDisconnected(self, entity, attribute, old, new, kwargs) -> None:
        self.cableConnected = False


    def isConnected(self):
        if self.charger_sensor:
            if self.ADapi.get_state(self.charger_sensor) == 'on':
                self.cableConnected = True
            elif self.ADapi.get_state(self.charger_sensor) == 'off':
                self.cableConnected = False
        return self.cableConnected


    def asleep(self) -> bool:
        if self.asleep_sensor:
            return self.ADapi.get_state(self.asleep_sensor) == 'on'
        return False


    def wakeMeUp(self) -> None:
        pass


    def isOnline(self) -> bool:
        if self.online_sensor:
            return self.ADapi.get_state(self.online_sensor) == 'on'
        return True


    def getLocation(self) -> str:
        if self.location_tracker:
            return self.ADapi.get_state(self.location_tracker)
        return 'home'


    def SoftwareUpdates(self) -> bool:
        # Return true if car is updating software.
        return False


    def forceDataUpdate(self) -> None:
        pass


    def polling_of_data(self) -> bool:
        if self.polling_switch:
            return self.ADapi.get_state(self.polling_switch) == 'on'
        return True


    def recentlyUpdated(self) -> bool:
        if self.data_last_update_time:
            last_update = self.ADapi.convert_utc(self.ADapi.get_state(self.data_last_update_time))
            now: datetime = self.ADapi.datetime(aware=True)
            stale_time: timedelta = now - last_update
            if stale_time < datetime.timedelta(minutes = 12):
                return False
        return True


    def kWhRemaining(self) -> float:
        if (
            self.battery_sensor
            and self.charge_limit
        ):
            battery_pct = self.ADapi.get_state(self.battery_sensor)
            limit_pct = self.ADapi.get_state(self.charge_limit)
            if (
                battery_pct != 'unavailable'
                and limit_pct != 'unavailable'
            ):
                try:
                    battery_pct = float(battery_pct)
                    limit_pct = float(limit_pct)
                except ValueError as ve:
                    self.ADapi.log(
                        f"Not able to calculate kWh Remaining To Charge based on battery: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                        f"Return existing value: {self.kWhRemainToCharge}. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                    return self.kWhRemainToCharge
                except TypeError as te:
                    self.ADapi.log(
                        f"Not able to calculate kWh Remaining To Charge based on battery: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                        f"Return existing value: {self.kWhRemainToCharge}. TypeError: {te}",
                        level = 'INFO'
                    )
                    return self.kWhRemainToCharge
                except Exception as e:
                    self.ADapi.log(
                        f"Not able to calculate kWh Remaining To Charge based on battery: {battery_pct} and limit: {limit_pct} for {self.carName}. "
                        f"Return existing value: {self.kWhRemainToCharge}. Exception: {e}",
                        level = 'WARNING'
                    )
                    return self.kWhRemainToCharge

                if battery_pct < limit_pct:
                    percentRemainToCharge = limit_pct - battery_pct
                    self.kWhRemainToCharge = (percentRemainToCharge / 100) * self.battery_size
                else:
                    self.kWhRemainToCharge = -1
                return self.kWhRemainToCharge
            else:
                return self.kWhRemainToCharge
        else:
            # Calculate remaining to charge based on max kWh Charged and session energy in charger class
            return -2


    def state_of_charge(self) -> int:
        SOC = -1
        try:
            SOC = float(self.ADapi.get_state(self.battery_sensor))
        except ValueError as ve:
            self.ADapi.log(
                f"{self.carName} Not able to get SOC. Trying alternative calculations. ValueError: {ve}",
                level = 'DEBUG'
            )
        except TypeError as te:
            self.ADapi.log(
                f"{self.carName} Not able to get SOC. Trying alternative calculations. TypeError: {te}",
                level = 'DEBUG'
            )
        except Exception as e:
            self.ADapi.log(
                f"{self.carName} Not able to get SOC. Trying alternative calculations. Exception: {e}",
                level = 'WARNING'
            )
        if SOC == -1:
            if self.kWhRemainToCharge == -1:
                SOC = 100
            else: # TODO: Find a way to calculate
                SOC = 10

        return SOC


    def changeChargeLimit(self, chargeLimit:int = 100 ) -> None:
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit)
        self.ADapi.set_state(self.charge_limit, state = chargeLimit)



class Tesla_charger(Charger, Car):
    """ Tesla
        Child class of Charger. Uses Tesla custom integration. https://github.com/alandtse/tesla Easiest installation is via HACS.
    
        Selection of possible commands to API
            self.ADapi.call_service('tesla_custom/api', command = 'STOP_CHARGE', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_LIMIT', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'percent': '70'} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_MAX', parameters = { 'path_vars': {'vehicle_id': self.charger_id}} )  #?
            self.ADapi.call_service('tesla_custom/api', command = 'CHARGING_AMPS', parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': '25'} )

        States returned from charger sensor is:
            if self.get_state(self.charger_sensor, attribute = 'charging_state') != 'Complete': #'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected'
    """

    def __init__(self, api,
        Car,
        namespace,
        charger, # Name of your tesla

        charger_sensor, # Binary_sensor.NAME_charger with attributes with status
        charger_switch, # Switch Charging or not
        charging_amps, # Input Number Amps to charge
        charger_power, # Charger power in kW. Contains volts and phases
        session_energy, # Charged this session. In kWh

        priority:int, # Priority. See full description
        finishByHour, # HA input_number for when car should be finished charging
        charge_now, # HA input_boolean to bypass smartcharge if true
        charge_on_solar,
        departure, # HA input_datetime for when to have car finished charging to 100%. To be written.
        guest
    ):

        charger_id = api.get_state(Car.online_sensor,
            attribute = 'id'
        )
        volts:int = 220
        phases:int = 1
        self.maxChargerAmpere:int = 0

        if Car.getLocation() == 'home':
            volts = api.get_state(charger_power,
                attribute = 'charger_volts')
            try:
                volts = math.ceil(float(volts))
            except (ValueError, TypeError):
                pass
            except Exception as e:
                api.log(
                    f"Error trying to get voltage: {api.get_state(charger_power, attribute = 'charger_volts')}. "
                    f"Exception: {e}", level = 'WARNING'
                )

            phases = api.get_state(charger_power,
                attribute = 'charger_phases')
            try:
                phases = int(phases)
            except (ValueError, TypeError):
                pass
            except Exception as e:
                api.log(f"Error trying to get phases: "
                    f"{(api.get_state(charger_power, attribute = 'charger_phases'))}. "
                    f"Exception: {e}", level = 'WARNING'
                )

        super().__init__(
            api = api,
            Car = Car,
            namespace = namespace,
            charger = charger,
            charger_id = charger_id,
            charger_sensor = charger_sensor,
            charger_switch = charger_switch,
            charging_amps = charging_amps,
            charger_power = charger_power,
            session_energy = session_energy,
            volts = volts,
            phases = phases,
            priority = priority,
            finishByHour = finishByHour,
            charge_now = charge_now,
            charge_on_solar = charge_on_solar,
            departure = departure,
            guest = None
        )

        self.setmaxChargingAmps()

        self.ADapi.listen_state(self.ChargingStarted, self.charger_switch, new = 'on')
        self.ADapi.listen_state(self.ChargingStopped, self.charger_switch, new = 'off')
        self.ADapi.listen_state(self.ChargingConnected, self.charger_sensor)

        """ End initialization Tesla Charger Class
        """


    def getChargingState(self) -> str:
        #Valid returns:
        #'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting'
        # TODO: Return someting valid if unavailable
        try:
            state = self.ADapi.get_state(self.charger_sensor, attribute = 'charging_state')
            if state == 'Starting':
                state = 'Charging'
            return state
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Could not getChargingState: {self.ADapi.get_state(self.charger_sensor)} ValueError: {ve}",
                level = 'DEBUG'
            )
            return None
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Could not getChargingState: {self.ADapi.get_state(self.charger_sensor)} TypeError: {te}",
                level = 'DEBUG'
            )
            return None
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not getChargingState: {self.ADapi.get_state(self.charger_sensor)} Exception: {e}",
                level = 'WARNING'
            )
            return None


    def setmaxChargingAmps(self) -> None:
        if (
            self.Car.getLocation() == 'home'
            and self.charger_id
        ):
            if self.ADapi.get_state(self.charging_amps) != 'unavailable':
                try:
                    maxChargerAmpere = math.ceil(float(self.ADapi.get_state(self.charging_amps, attribute = 'max')))
                except ValueError as ve:
                    self.ADapi.log(
                        f"{self.charger} Could not get maxChargingAmps. ValueError: {ve}",
                        level = 'DEBUG'
                    )
                except TypeError as te:
                    self.ADapi.log(
                        f"{self.charger} Could not get maxChargingAmps. TypeError: {te}",
                        level = 'DEBUG'
                    )
                except Exception as e:
                    self.ADapi.log(
                        f"{self.charger} Could not get maxChargingAmps. Exception: {e}",
                        level = 'WARNING'
                    )
                
                updateFile = False
                with open(JSON_PATH, 'r') as json_read:
                    ElectricityData = json.load(json_read)

                if not 'MaxAmp' in ElectricityData['charger'][self.charger_id]:
                    self.maxChargerAmpere = maxChargerAmpere
                    ElectricityData['charger'][self.charger_id].update(
                        {"MaxAmp" : self.maxChargerAmpere}
                    )
                    updateFile = True

                elif maxChargerAmpere > int(ElectricityData['charger'][self.charger_id]['MaxAmp']):
                    self.maxChargerAmpere = maxChargerAmpere
                    ElectricityData['charger'][self.charger_id].update(
                        {"MaxAmp" : self.maxChargerAmpere}
                    )
                    updateFile = True

                if not 'CarLimitAmpere' in ElectricityData['charger'][self.Car.vehicle_id]:
                    self.Car.car_limit_max_charging = maxChargerAmpere
                    ElectricityData['charger'][self.Car.vehicle_id].update(
                        {"CarLimitAmpere" : maxChargerAmpere}
                    )
                    updateFile = True

                elif maxChargerAmpere > int(ElectricityData['charger'][self.Car.vehicle_id]['CarLimitAmpere']):
                    self.Car.car_limit_max_charging = maxChargerAmpere
                    ElectricityData['charger'][self.Car.vehicle_id].update(
                        {"CarLimitAmpere" : self.Car.car_limit_max_charging}
                    )
                    updateFile = True
                    
                if updateFile:
                    with open(JSON_PATH, 'w') as json_write:
                        json.dump(ElectricityData, json_write, indent = 4)
                

                # Set Voltphase also
                if self.voltPhase == 220:
                    volts:int = 220
                    phases:int = 1
                    volts = self.ADapi.get_state(self.charger_power,
                        attribute = 'charger_volts')
                    try:
                        volts = math.ceil(float(volts))
                    except (ValueError, TypeError):
                        pass
                    except Exception as e:
                        self.ADapi.log(
                            f"Error trying to get voltage: {self.ADapi.get_state(self.charger_power, attribute = 'charger_volts')}. "
                            f"Exception: {e}", level = 'WARNING'
                        )

                    phases = self.ADapi.get_state(self.charger_power,
                        attribute = 'charger_phases')
                    try:
                        phases = int(phases)
                    except (ValueError, TypeError):
                        return
                    except Exception as e:
                        self.ADapi.log(f"Error trying to get phases: "
                            f"{(self.ADapi.get_state(self.charger_power, attribute = 'charger_phases'))}. "
                            f"Exception: {e}", level = 'WARNING'
                        )
                    self.setVoltPhase(volts = volts, phases = phases)


    def setChargingAmps(self, charging_amp_set:int = 16) -> None:
        charging_amp_set = super().setChargingAmps(charging_amp_set = charging_amp_set)
        self.ADapi.call_service('tesla_custom/api',
            command = 'CHARGING_AMPS',
            parameters = {'path_vars': {'vehicle_id': self.charger_id}, 'charging_amps': charging_amp_set}
        )


    def ChargingConnected(self, entity, attribute, old, new, kwargs) -> None:
        global CHARGE_SCHEDULER
        self.setmaxChargingAmps()

        if (
            new == 'on'
            and self.Car.getLocation() == 'home'
            and self.kWhRemaining() > 0
        ):
            if self.ADapi.get_state(self.charger_switch) == 'on':
                return # Calculations will be handeled by ChargingStarted

            if self.findNewChargeTime():
                self.startCharging()
            elif self.hasChargingScheduled():
                if CHARGE_SCHEDULER.chargingStart - datetime.timedelta(minutes=12) > datetime.datetime.now():
                    self.stopCharging()

        elif new == 'off':
            if self.hasChargingScheduled():
                CHARGE_SCHEDULER.removeFromQueue(charger_id = self.charger_id)
                self.turnOff_Charge_now()
            if self.max_range_handler != None:
                # TODO: Program charging to max at departure time.
                # @HERE: Call a function that will cancel handler when car is disconnected
                #self.ADapi.run_in(self.resetMaxRangeCharging, 1)
                self.ADapi.log(f"{self.charger} Has a max_range_handler. Not Programmed yet", level = 'DEBUG')



    def startCharging(self) -> None:
        if super().startCharging():
            try:
                self.ADapi.call_service('tesla_custom/api',
                    command = 'START_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
                self.Car.forceDataUpdate()
                #self.ADapi.call_service('switch/turn_on', entity_id = self.charger_switch)
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception: {e}", level = 'WARNING')


    def stopCharging(self) -> None:
        if super().stopCharging():
            try:
                self.ADapi.call_service('tesla_custom/api',
                    command = 'STOP_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
                self.Car.forceDataUpdate()
                # Alternative: self.ADapi.call_service('switch/turn_off', entity_id = self.charger_switch)
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging: {e}", level = 'WARNING')


    def checkIfChargingStarted(self, kwargs) -> None:
        if not super().checkIfChargingStarted(0):
            self.Car.forceDataUpdate()
            try:
                self.ADapi.call_service('tesla_custom/api',
                    command = 'START_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Start Charging in checkIfChargingStarted for {self.charger}. Exception: {e}",
                    level = 'DEBUG'
                )


    def checkIfChargingStopped(self, kwargs) -> None:
        if not super().checkIfChargingStopped(0):
            self.Car.forceDataUpdate()
            try:
                self.ADapi.call_service('tesla_custom/api',
                    command = 'STOP_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.charger_id}, 'wake_if_asleep': True}
                )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Stop Charging in checkIfChargingStopped for {self.charger}. Exception: {e}",
                    level = 'DEBUG'
                )



class Tesla_car(Car):
    """ Tesla
        Child class of Car. Uses Tesla custom integration. https://github.com/alandtse/tesla Easiest installation is via HACS.
    
        Selection of possible commands to API
            self.ADapi.call_service('tesla_custom/api', command = 'STOP_CHARGE', parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep': True} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_LIMIT', parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'percent': '70'} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_MAX', parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}} )  #?
            self.ADapi.call_service('tesla_custom/api', command = 'CHARGING_AMPS', parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'charging_amps': '25'} )

        States returned from charger sensor is:
            if self.get_state(self.charger_sensor, attribute = 'charging_state') != 'Complete': #'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected'
    """

    def __init__(self, api,
        namespace,
        carName, # Unique name of charger/car
        charger_sensor, # Sensor chargecable connected
        charge_limit, # SOC limit sensor
        battery_sensor, # SOC (State Of Charge)
        asleep_sensor, # If car is sleeping
        online_sensor, # If car is online
        location_tracker, # Location of car/charger
        destination_location_tracker, # Destination of car
        arrival_time, # Sensor with Arrival time, estimated energy at arrival and destination.
        software_update, # If Tesla updates software it can`t change or stop charging
        force_data_update, # Button to force car to send update to HA
        polling_switch,
        data_last_update_time,
        battery_size:int, # User input size of battery. Used to calculate amount of time to charge
        pref_charge_limit:int # User input if prefered SOC limit is other than 90%
    ):

        self.vehicle_id = api.get_state(online_sensor,
            attribute = 'id'
        )

        super().__init__(
            api = api,
            namespace = namespace,
            carName = carName,
            charger_sensor = charger_sensor,
            charge_limit = charge_limit,
            battery_sensor = battery_sensor,
            asleep_sensor = asleep_sensor,
            online_sensor = online_sensor,
            location_tracker = location_tracker,
            destination_location_tracker = destination_location_tracker,
            arrival_time = arrival_time,
            software_update = software_update,
            force_data_update = force_data_update,
            polling_switch = polling_switch,
            data_last_update_time = data_last_update_time,
            battery_size = battery_size,
            pref_charge_limit = pref_charge_limit
        )

        """ End initialization Tesla Car Class
        """


    def wakeMeUp(self) -> None:
        if self.ADapi.get_state(self.polling_switch) == 'on':
            if (
                self.ADapi.get_state(self.charger_sensor) != 'Complete'
                and self.ADapi.get_state(self.charger_sensor) != 'Disconnected'
            ):
                if not self.recentlyUpdated():
                    self.ADapi.call_service('tesla_custom/api',
                        command = 'WAKE_UP',
                        parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep' : True}
                    )


    def SoftwareUpdates(self) -> bool:
        if (
            self.ADapi.get_state(self.software_update) != 'unknown'
            and self.ADapi.get_state(self.software_update) != 'unavailable'
        ):
            if self.ADapi.get_state(self.software_update, attribute = 'in_progress') != False:
                return True
        return False


    def forceDataUpdate(self) -> None:
        self.ADapi.call_service('button/press',
            entity_id = self.force_data_update
        )


    def changeChargeLimit(self, chargeLimit:int = 90 ) -> None:
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit)
        self.ADapi.call_service('tesla_custom/api',
            command = 'CHANGE_CHARGE_LIMIT',
            parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'percent': chargeLimit}
        )



class Easee(Charger):
    """ Easee
        Child class of Charger. Uses Easee EV charger component for Home Assistant. https://github.com/nordicopen/easee_hass 
        Easiest installation is via HACS.

    """

    def __init__(self, api,
        Car,
        namespace,
        charger, # Name of your Easee
        charger_sensor, # sensor.charger_status
        reason_for_no_current, # No switch in Easee integration
        charging_amps, # Input Number Amps to charge
        charger_power, # Charger power in kW
        session_energy, # Charged this session. In kWh
        voltage,
        max_charger_limit,
        priority:int, # Priority. See full description
        finishByHour, # HA input_number for when car should be finished charging
        charge_now, # HA input_boolean to bypass smartcharge if true
        charge_on_solar,
        departure, # HA input_datetime for when to have car finished charging to 100%. To be written.
        guest
    ):

        charger_id:str = api.get_state(charger_sensor,
            attribute = 'id'
        )

        self.reason_for_no_current = reason_for_no_current

        volts = api.get_state(voltage)
        try:
            volts = math.ceil(float(volts))
        except ValueError:
            volts = 220
        except Exception as e:
            api.log(f"Error trying to get voltage: {api.get_state(voltage)}. Exception: {e}", level = 'WARNING')

        phases = (api.get_state(charger_sensor,
                attribute = 'config_phaseMode')
            )
        try:
            phases = math.ceil(float(phases))
        except ValueError:
            phases = 1
        except Exception as e:
            api.log(f"Error trying to get phases: "
                f"{(api.get_state(charger_sensor, attribute = 'config_phaseMode'))}. "
                f"Exception: {e}", level = 'WARNING'
            )

        if api.get_state(max_charger_limit) != 'unavailable':
            self.maxChargerAmpere:int = math.ceil(float(api.get_state(max_charger_limit)))


        super().__init__(
            api = api,
            Car = Car,
            namespace = namespace,
            charger = charger,
            charger_id = charger_id,
            charger_sensor = charger_sensor,
            charger_switch = None,
            charging_amps = charging_amps,
            charger_power = charger_power,
            session_energy = session_energy,
            volts = volts,
            phases = phases,
            priority = priority,
            finishByHour = finishByHour,
            charge_now = charge_now,
            charge_on_solar = charge_on_solar,
            departure = departure,
            guest = guest
        )

        api.listen_state(self.statusChange, charger_sensor)
        api.listen_state(self.reasonChange, self.reason_for_no_current)

        global JSON_PATH
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not 'MaxAmp' in ElectricityData['charger'][self.charger_id]:
            ElectricityData['charger'][self.charger_id].update(
                {"MaxAmp" : self.maxChargerAmpere}
            )
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)

        """ End initialization Easee Charger Class
        """


        #'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status
        # Return: Charging / Complete / 'Disconnected' / 'NoPower' / 'Stopped' / 'Starting'
    def getChargingState(self) -> str:
        status = self.ADapi.get_state(self.charger_sensor)
        if status == 'charging':
            return 'Charging'
        elif status == 'completed':
            return 'Complete'
        elif status == 'awaiting_start':
            return 'Stopped'
        elif status == 'disconnected':
            return 'Disconnected'
        elif not status == 'ready_to_charge':
            self.ADapi.log(f"Status: {status} for {self.charger} is not defined", level = 'WARNING')
        return status


        # Listen states
        #'awaiting_start' / 'charging' / 'completed' / 'disconnected' / 'ready_to_charge' / from charger_status
    def statusChange(self, entity, attribute, old, new, kwargs) -> None:
        global CHARGE_SCHEDULER
        global JSON_PATH

        if (
            new == 'awaiting_start'
            and old == 'disconnected'
        ):
            self.Car.cableConnected = True
            if not self.findNewChargeTime():
               self.stopCharging()

        elif (
            new == 'charging'
            and old == 'completed'
        ):
            pass # Preheating...

        elif new == 'charging':
            if not self.hasChargingScheduled():
                if not self.findNewChargeTime():
                    self.stopCharging()

            elif not CHARGE_SCHEDULER.isChargingTime():
                self.stopCharging()

        elif new == 'completed':
            CHARGE_SCHEDULER.removeFromQueue(charger_id = self.charger_id)
            self.turnOff_Charge_now()

            if self.session_energy:
                if self.guestCharging:
                    return

                session = float(self.ADapi.get_state(self.session_energy))
                if self.Car.maxkWhCharged < session:
                    self.Car.maxkWhCharged = session
                        # Find max kWh charged from charger during one session.
                    with open(JSON_PATH, 'r') as json_read:
                        ElectricityData = json.load(json_read)

                    ElectricityData['charger'][self.charger_id].update(
                        {"MaxkWhCharged" : self.Car.maxkWhCharged}
                    )
                    with open(JSON_PATH, 'w') as json_write:
                        json.dump(ElectricityData, json_write, indent = 4)

        elif new == 'disconnected':
            CHARGE_SCHEDULER.removeFromQueue(charger_id = self.charger_id)
            self.turnOff_Charge_now()
            self.Car.cableConnected = False


        #'no_current_request' / 'undefined' / 'waiting_in_queue' / 'limited_by_charger_max_limit' / 'limited_by_local_adjustment' / 'limited_by_car' from reason_for_no_current
        # 'car_not_charging' / 
    def reasonChange(self, entity, attribute, old, new, kwargs) -> None:
        global JSON_PATH

        if new == 'limited_by_car':
            if self.guestCharging:
                return

            chargingAmpere = math.ceil(float(self.ADapi.get_state(self.charging_amps)))
            if self.Car.car_limit_max_charging != chargingAmpere:
                self.Car.car_limit_max_charging = chargingAmpere
                with open(JSON_PATH, 'r') as json_read:
                    ElectricityData = json.load(json_read)

                ElectricityData['charger'][self.Car.vehicle_id].update(
                    { "CarLimitAmpere" : self.Car.car_limit_max_charging}
                )
                with open(JSON_PATH, 'w') as json_write:
                    json.dump(ElectricityData, json_write, indent = 4)


    def setChargingAmps(self, charging_amp_set:int = 16) -> None:
        charging_amp_set = super().setChargingAmps(charging_amp_set = charging_amp_set)
        self.ADapi.call_service('easee/set_charger_dynamic_limit',
            current = charging_amp_set,
            charger_id = self.charger_id
        )


    def startCharging(self) -> None:
        if super().startCharging():
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'resume',
                    charger_id = self.charger_id
                ) # start
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception {e}", level = 'WARNING')


    def stopCharging(self) -> None:
        if not self.dontStopMeNow():
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'pause',
                    charger_id = self.charger_id
                ) # stop
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging. Exception: {e}", level = 'WARNING')

        elif (
            not self.dontStopMeNow()
            and self.ADapi.get_state(self.charger_sensor) == 'awaiting_start'
        ):
            if self.checkCharging_handler != None:
                if self.ADapi.timer_running(self.checkCharging_handler):
                    try:
                        self.ADapi.cancel_timer(self.checkCharging_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not possible to stop timer to check if charging started/stopped. Exception: {e}",
                            level = 'DEBUG'
                        )
                    finally:
                        self.checkCharging_handler = None
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)

            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'pause',
                    charger_id = self.charger_id
                ) # stop
            except Exception as e:
                self.ADapi.log(
                    f"{self.charger} Could not Stop Charging while awaiting start. Exception: {e}",
                    level = 'WARNING'
                )


    def checkIfChargingStarted(self, kwargs) -> None:
        if not super().checkIfChargingStarted(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'resume',
                    charger_id = self.charger_id
                    ) # start
            except Exception as e:
                self.ADapi.log(
                    f"Could not Start Charging in checkIfChargingStarted for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )


    def checkIfChargingStopped(self, kwargs) -> None:
        if not super().checkIfChargingStopped(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'pause',
                    charger_id = self.charger_id
                    ) # stop
            except Exception as e:
                self.ADapi.log(
                    f"Could not Stop Charging in checkIfChargingStopped for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )


class Heater:
    """ Heater
        Parent class for on_off_switch and electrical heaters
        Sets up times to save/spend based on electricity price
    """

    def __init__(self,
        api,
        heater,
        consumptionSensor,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        namespace,
        away,
        automate,
        recipient
    ):

        self.ADapi = api

        self.heater = heater # on_off_switch boiler or heater switch
        self.automate = automate # Switch to disable automation

            # Vacation setup
        self.namespace = namespace
        if not self.namespace:
            self.away_state = self.ADapi.get_state(away)  == 'on'
            self.ADapi.listen_state(self.awayStateListen, away)
        else:
            self.away_state = self.ADapi.get_state(away, namespace = self.namespace)  == 'on'

            self.ADapi.listen_state(self.awayStateListen, away,
                namespace = self.namespace
            )

            # Notification setup
        if recipient:
            self.recipients = recipient
        else:
            global RECIPIENTS
            self.recipients = RECIPIENTS

            # Consumption sensors and setups
        self.consumptionSensor = consumptionSensor
        self.kWhconsumptionSensor = kWhconsumptionSensor
        self.prev_consumption:int = 0
        self.max_continuous_hours:int = max_continuous_hours
        self.on_for_minimum:int = on_for_minimum
        self.pricedrop:float = pricedrop

            # Consumption data
        self.time_to_save:list = []
        self.time_to_spend:list = []
        self.off_for_hours:int = 0
        self.consumption_when_turned_on:float = 0.0
        self.isOverconsumption:bool = False
        self.increase_now:bool = False
        self.normal_power:int = 0
        self.findConsumptionAfterTurnedOn_Handler = None

            # Persistent storage for consumption logging
        global JSON_PATH
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.heater in ElectricityData['consumption']:
            ElectricityData['consumption'].update(
                {self.heater : {"ConsumptionData" : {}}}
            )
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
        else:
            consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData']
            self.normal_power = float(self.ADapi.get_state(self.consumptionSensor))

            if self.normal_power > 100:
                if not "power" in ElectricityData['consumption'][self.heater]:
                    ElectricityData['consumption'][self.heater].update(
                        {"power" : self.normal_power}
                    )
                    with open(JSON_PATH, 'w') as json_write:
                        json.dump(ElectricityData, json_write, indent = 4)
            elif "power" in ElectricityData['consumption'][self.heater]:
                self.normal_power = ElectricityData['consumption'][self.heater]['power']

            # Get prices to set up automation times
        self.ADapi.run_in(self.heater_getNewPrices, 60)


    def awayStateListen(self, entity, attribute, old, new, kwargs) -> None:
        if not self.namespace:
            self.away_state = self.ADapi.get_state(entity) == 'on'
        else:
            self.away_state = self.ADapi.get_state(entity, namespace = self.namespace) == 'on'
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def heater_getNewPrices(self, kwargs) -> None:
        global ELECTRICITYPRICE
        self.time_to_save = ELECTRICITYPRICE.findpeakhours(
            pricedrop = self.pricedrop,
            max_continuous_hours = self.max_continuous_hours,
            on_for_minimum = self.on_for_minimum
        )

        if (
            self.ADapi.now_is_between('04:00:00', '14:00:00')
            and len(ELECTRICITYPRICE.elpricestoday) == 24
        ):
            self.HeatAt = datetime.datetime.today().replace(hour = 22, minute = 0, second = 0, microsecond = 0)
            self.EndAt = datetime.datetime.today().replace(hour = 23, minute = 0, second = 0, microsecond = 0)
        else:
            self.HeatAt, self.EndAt, price = ELECTRICITYPRICE.getContinuousCheapestTime(
                hoursTotal = 3,
                calculateBeforeNextDayPrices = False,
                startTime = datetime.datetime.today().hour
            )
        self.ADapi.run_in(self.heater_setNewValues, 5)


        """Logging purposes to check what hours heater turns off/down to check if behaving as expected"""
        #if len(self.time_to_save) > 0:
        #    self.ADapi.log(f"{self.heater}: {ELECTRICITYPRICE.print_peaks(self.time_to_save)}", level = 'INFO')


    def heater_setNewValues(self, kwargs) -> None:
        isOn:bool = self.ADapi.get_state(self.heater) == 'on'
        if (
            self.isOverconsumption
            and isOn
        ):
            self.ADapi.turn_off(self.heater)
            return

        if self.increase_now:
            if not isON:
                self.ADapi.turn_on(self.heater)
            return

        if not self.away_state:
            if datetime.datetime.today().replace(minute=0, second=0, microsecond=0) in self.time_to_save:
                if isOn:
                    self.ADapi.turn_off(self.heater)
            elif not isOn:
                self.ADapi.turn_on(self.heater)
        elif (
            datetime.datetime.today() > self.HeatAt
            and datetime.datetime.today() < self.EndAt
        ):
            if not isOn:
                self.ADapi.turn_on(self.heater)
        elif isOn:
            self.ADapi.turn_off(self.heater)


        # Functions called from electrical
    def setPreviousState(self) -> None:
        self.isOverconsumption = False
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def setSaveState(self) -> None:
        self.isOverconsumption = True
        self.ADapi.run_in(self.heater_setNewValues, 1)


    def setIncreaseState(self) -> None:
        self.increase_now = True
        self.ADapi.run_in(self.heater_setNewValues, 1)


        # Functions to calculate and log consumption to persistent storage
    def findConsumptionAfterTurnedOn(self, kwargs) -> None:
        try:
            self.consumption_when_turned_on = float(self.ADapi.get_state(self.kWhconsumptionSensor))
        except ValueError:
            self.ADapi.log(f"{self.kWhconsumptionSensor} unavailable in finding consumption", level = 'DEBUG')
        if self.findConsumptionAfterTurnedOn_Handler != None:
            if self.ADapi.timer_running(self.findConsumptionAfterTurnedOn_Handler):
                try:
                    self.ADapi.cancel_timer(self.findConsumptionAfterTurnedOn_Handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not able to stop findConsumptionAfterTurnedOn_Handler for {self.heater}. Exception: {e}",
                        level = "DEBUG"
                    )

        self.findConsumptionAfterTurnedOn_Handler = None
        self.ADapi.listen_state(self.registerConsumption, self.consumptionSensor,
            constrain_state=lambda x: float(x) < 20,
            oneshot = True
        )

    def registerConsumption(self, entity, attribute, old, new, kwargs) -> None:
        global JSON_PATH
        global OUT_TEMP
        try:
            if self.ADapi.get_state(self.heater) == 'on':
                with open(JSON_PATH, 'r') as json_read:
                    ElectricityData = json.load(json_read)

                consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData']
                out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
                consumption = float(self.ADapi.get_state(self.kWhconsumptionSensor)) - self.consumption_when_turned_on
                offForHours = str(self.off_for_hours)

                if consumption > 0:
                    if not "power" in ElectricityData['consumption'][self.heater]:
                        ElectricityData['consumption'][self.heater].update(
                            {"power" : float(old)}
                        )

                    if not offForHours in ElectricityData['consumption'][self.heater]['ConsumptionData']:
                        newData = {"Consumption" : consumption, "Counter" : 1}
                        ElectricityData['consumption'][self.heater]['ConsumptionData'].update(
                            {offForHours : {out_temp_str : newData}}
                        )
                    elif not out_temp_str in ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours]:
                        newData = {"Consumption" : round(consumption,2), "Counter" : 1}
                        ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours].update(
                            {out_temp_str : newData}
                        )
                    else:
                        consumptionData = ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours][out_temp_str]
                        counter = consumptionData['Counter'] + 1
                        if counter > 100:
                            return

                        avgConsumption = round(((consumptionData['Consumption'] * consumptionData['Counter']) + consumption) / counter,2)
                        newData = {"Consumption" : avgConsumption, "Counter" : counter}
                        ElectricityData['consumption'][self.heater]['ConsumptionData'][offForHours].update(
                            {out_temp_str : newData}
                        )

                    with open(JSON_PATH, 'w') as json_write:
                        json.dump(ElectricityData, json_write, indent = 4)
        except Exception as e:
            self.ADapi.log(
                f"Not able to register consumption for {self.heater}. Exception: {e}",
                level = "DEBUG"
            )


        # Helper functions for windows
    def windowOpened(self, entity, attribute, old, new, kwargs) -> None:
        if self.numWindowsOpened() != 0:
            self.windows_is_open = True
            self.notify_on_window_closed = True
            if self.automate:
                self.ADapi.turn_on(self.automate)
            self.ADapi.run_in(self.heater_setNewValues, 0)


    def windowClosed(self, entity, attribute, old, new, kwargs) -> None:
        if self.numWindowsOpened() == 0:
            self.windows_is_open = False
            self.notify_on_window_open = True
            self.ADapi.run_in(self.heater_setNewValues, 0)


    def numWindowsOpened(self) -> int:
        opened = 0
        for window in self.windowsensors:
            if self.ADapi.get_state(window) == 'on':
                opened += 1
        return opened


class Climate(Heater):
    """ Child class of Heater
        For controlling electrical heaters to heat off peak hours.
    """

    def __init__(self,
        api,
        heater,
        consumptionSensor,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        namespace,
        away,
        automate,
        recipient,
        indoor_sensor_temp,
        target_indoor_temp:float,
        rain_level:float,
        anemometer_speed:int,
        low_price_max_continuous_hours:int,
        priceincrease:float,
        windowsensors:list,
        daytime_savings:list,
        temperatures:list
    ):

        self.indoor_sensor_temp = indoor_sensor_temp
        self.target_indoor_temp:float = target_indoor_temp
        self.rain_level:float = rain_level
        self.anemometer_speed:int = anemometer_speed
        self.low_price_max_continuous_hours:int = low_price_max_continuous_hours
        self.priceincrease:float = priceincrease
        self.windowsensors:list = windowsensors
        self.daytime_savings:list = daytime_savings
        self.temperatures:list = temperatures

        super().__init__(
            api = api,
            heater = heater,
            consumptionSensor = consumptionSensor,
            kWhconsumptionSensor = kWhconsumptionSensor,
            max_continuous_hours = max_continuous_hours,
            on_for_minimum = on_for_minimum,
            pricedrop = pricedrop,
            namespace = namespace,
            away = away,
            automate = automate,
            recipient = recipient
        )

        for windows in self.windowsensors:
            self.ADapi.listen_state(self.windowOpened, windows,
                new = 'on',
                duration = 120
            )
            self.ADapi.listen_state(self.windowClosed, windows,
                new = 'off'
            )

        self.windows_is_open:bool = False
        for window in self.windowsensors:
            if self.ADapi.get_state(window) == 'on':
                self.windows_is_open = True

        self.notify_on_window_open:bool = True
        self.notify_on_window_closed:bool = False

        runtime = datetime.datetime.now()
        addseconds = (round((runtime.minute*60 + runtime.second)/1200)+1)*1200
        runtime = runtime.replace(minute=0, second=10, microsecond=0) + datetime.timedelta(seconds=addseconds)
        self.ADapi.run_every(self.heater_setNewValues, runtime, 1200)

            # Warnings 
        if not indoor_sensor_temp:
            self.ADapi.log(
                f"No external indoor temperature sensor for {heater} configured. Automation will not check if it is hot inside.",
                level = 'INFO'
            )


        # Get new prices to save and in addition to turn up heat for heaters before expensive hours
    def heater_getNewPrices(self, kwargs) -> None:
        global ELECTRICITYPRICE
        super().heater_getNewPrices(0)
        self.time_to_spend = ELECTRICITYPRICE.findLowPriceHours(
            priceincrease = self.priceincrease,
            max_continuous_hours = self.low_price_max_continuous_hours
        )


        """Logging purposes to check what hours heating will be turned up"""
        #if self.time_to_spend:
        #    self.ADapi.log(f"{self.heater} Extra heating at: {ELECTRICITYPRICE.print_peaks(self.time_to_spend)}", level = 'INFO')


    def awayStateListen(self, entity, attribute, old, new, kwargs) -> None:
        if not self.namespace:
            self.away_state = self.ADapi.get_state(entity) == 'on'
        else:
            self.away_state = self.ADapi.get_state(entity, namespace = self.namespace) == 'on'
        if (
            self.ADapi.get_state(self.heater) == 'off'
            and new == 'off'
        ):
            try:
                self.ADapi.call_service('climate/set_hvac_mode',
                    entity_id = self.heater,
                    hvac_mode = 'heat'
                )
            except Exception as e:
                self.ADapi.log(f"Not able to set hvac_mode to heat for {self.heater}. Exception: {e}", level = 'INFO')
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def find_target_temperatures(self) -> int:
        """ Helper function to find correct dictionary element in temperatures
        """
        global OUT_TEMP
        target_num = 0
        for target_num, target_temp in enumerate(self.temperatures):
            if target_temp['out'] >= OUT_TEMP:
                if target_num != 0:
                    target_num -= 1
                return target_num

        return target_num


        # Functions to set temperature
    def setSaveState(self) -> None:
        self.isOverconsumption = True
        target_num = self.find_target_temperatures()
        target_temp = self.temperatures[target_num]
        if self.ADapi.get_state(self.heater) == 'heat':
            try:
                if float(self.ADapi.get_state(self.heater, attribute='temperature')) > target_temp['away']:
                    self.ADapi.call_service('climate/set_temperature',
                        entity_id = self.heater,
                        temperature = target_temp['away']
                    )
            except (TypeError, AttributeError) as ve:
                self.ADapi.call_service('climate/set_temperature',
                    entity_id = self.heater,
                    temperature = 10
                )
                self.ADapi.log(f"Error when trying to set temperature to {self.heater}: {ve}", level = 'DEBUG')


    def heater_setNewValues(self, kwargs) -> None:
        global RAIN_AMOUNT
        global WIND_AMOUNT
        global OUT_TEMP

        if self.automate:
            if self.ADapi.get_state(self.automate) == 'off':
                return

        if (
            self.ADapi.get_state(self.heater) == 'off'
            or self.isOverconsumption
        ):
            return

        target_num = self.find_target_temperatures()
        target_temp = self.temperatures[target_num]

        try:
            heater_temp = float(self.ADapi.get_state(self.heater, attribute='temperature'))
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"Error when trying to get currently set temperature to {self.heater}: {ve}",
                level = 'DEBUG'
            )
            heater_temp = target_temp['normal']
        except Exception as e:
            self.ADapi.log(
                f"Error when trying to get currently set temperature to {self.heater}. Exception: {e}",
                level = 'INFO'
            )
            heater_temp = target_temp['normal']

        #Target temperature
        new_temperature = target_temp['normal']
        if RAIN_AMOUNT >= self.rain_level:
            new_temperature += 1
        elif WIND_AMOUNT >= self.anemometer_speed:
            new_temperature += 1

        # Windows
        if (
            not self.windows_is_open
            and self.notify_on_window_closed
            and float(self.ADapi.get_state(self.heater, attribute='current_temperature')) >= 27
            and OUT_TEMP > 20
        ):
            for r in self.recipients:
                self.ADapi.notify(
                    f"No Window near {self.heater} is open and it is getting hot inside! {self.ADapi.get_state(self.heater, attribute='current_temperature')}°",
                    title = "Window closed",
                    name = r
                )
            self.notify_on_window_closed = False
        
        if self.windows_is_open:
            new_temperature = target_temp['away']
            if (
                self.notify_on_window_open
                and float(self.ADapi.get_state(self.heater, attribute='current_temperature')) < target_temp['normal']
            ):
                for r in self.recipients:
                    self.ADapi.notify(
                        f"Window near {self.heater} is open and inside temperature is {self.ADapi.get_state(self.heater, attribute='current_temperature')}",
                        title = "Window open",
                        name = r
                    )
                self.notify_on_window_open = False
        
        elif self.increase_now:
            if 'spend' in target_temp:
                new_temperature = target_temp['spend']

        # Holliday temperature
        elif self.away_state:
            new_temperature = target_temp['away']

        # Low price for electricity
        elif 'spend' in target_temp:
            if datetime.datetime.today().replace(minute=0, second=0, microsecond=0) in self.time_to_spend:
                new_temperature = target_temp['spend']

        # Peak and savings temperature
        if datetime.datetime.today().replace(minute=0, second=0, microsecond=0) in self.time_to_save:
            if new_temperature > target_temp['save']:
                new_temperature = target_temp['save']
        
        # Daytime Savings
        doDaytimeSaving = False
        for daytime in self.daytime_savings:
            if (
                'start' in daytime
                and 'stop' in daytime
            ):
                if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                    doDaytimeSaving = True
                    if 'presence' in daytime:
                        for presence in daytime['presence']:
                            if self.ADapi.get_state(presence) == 'home':
                                doDaytimeSaving = False

            elif 'presence' in daytime:
                doDaytimeSaving = True
                for presence in daytime['presence']:
                    if self.ADapi.get_state(presence) == 'home':
                        doDaytimeSaving = False

        if (
            doDaytimeSaving
            and new_temperature > target_temp['save']
        ):
            new_temperature = target_temp['save']


        if self.indoor_sensor_temp:
            try:
                in_temp = float(self.ADapi.get_state(self.indoor_sensor_temp))
            except (TypeError, AttributeError):
                self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')
                in_temp = self.target_indoor_temp - 0.1
            except Exception as e:
                in_temp = self.target_indoor_temp - 0.1
                self.ADapi.log(f"Not able to set new inside temperature from {self.indoor_temp}. {e}", level = 'WARNING')

            if in_temp > self.target_indoor_temp:
                if new_temperature >= target_temp['normal'] -1:
                    if heater_temp == target_temp['normal']:
                        new_temperature -= 1
                    elif heater_temp > target_temp['normal']:
                        new_temperature = target_temp['normal']
                    elif (
                        heater_temp == target_temp['normal'] -1
                        and in_temp > self.target_indoor_temp +1
                    ):
                            new_temperature = target_temp['normal'] -2
                    elif heater_temp < target_temp['normal']:
                        if in_temp > self.target_indoor_temp + 1:
                            new_temperature = heater_temp
                        else:
                            new_temperature = target_temp['normal'] -1

        # Setting new temperature
        try:
            if heater_temp != new_temperature:
                self.ADapi.call_service('climate/set_temperature',
                entity_id = self.heater,
                temperature = new_temperature
            )
        except (TypeError, AttributeError):
            self.ADapi.log(f"{self.heater} has no temperature. Probably offline", level = 'DEBUG')


class On_off_switch(Heater):
    """ Child class of Heater
        Heating of on_off_switch off peak hours
        Turns on/off a switch depending og given input and electricity price
    """
    def __init__(self,
        api,
        heater,
        consumptionSensor,
        kWhconsumptionSensor,
        max_continuous_hours:int,
        on_for_minimum:int,
        pricedrop:float,
        namespace,
        away,
        automate,
        recipient
    ):

        self.daytime_savings:list = []

        super().__init__(
            api = api,
            heater = heater,
            consumptionSensor = consumptionSensor,
            kWhconsumptionSensor = kWhconsumptionSensor,
            max_continuous_hours = max_continuous_hours,
            on_for_minimum = on_for_minimum,
            pricedrop = pricedrop,
            namespace = namespace,
            away = away,
            automate = automate,
            recipient = recipient
        )

        self.ADapi.run_hourly(self.heater_setNewValues, datetime.time(0, 0, 2))


class Appliances:
    """ Appliances
        Starting of appliances when electricity price is lowest before next time defined by 'finishByHour'.
        If requested between '06:00:00', '15:00:00' it will try to finish before 15:00.
    """

    def __init__(self,
        api,
        remote_start,
        program,
        running_time:int,
        finishByHour:int
    ):

        self.ADapi = api
        self.handler = None

        self.program = program
        self.remote_start = remote_start
        self.running_time:int = running_time
        self.finishByHour:int = finishByHour

        self.ADapi.listen_state(self.remoteStartRequested, remote_start,
            new = 'on'
        )

        if self.ADapi.get_state(remote_start) == 'on':
            self.ADapi.run_in(self.findTimeForWashing,70)


    def remoteStartRequested(self, entity, attribute, old, new, kwargs) -> None:
        self.ADapi.run_in(self.findTimeForWashing,5)


    def findTimeForWashing(self, kwargs) -> None:
        global ELECTRICITYPRICE
        global RECIPIENTS
        startWashingAt, EndAt, price = ELECTRICITYPRICE.getContinuousCheapestTime(
            hoursTotal = self.running_time,
            calculateBeforeNextDayPrices = True,
            startTime = datetime.datetime.today().hour,
            finishByHour = self.finishByHour
        )

        if startWashingAt > datetime.datetime.today():
            self.resetHandler()
            self.handler = self.ADapi.run_at(self.startWashing, startWashingAt)
            self.ADapi.log(f"Starting appliance at {startWashingAt}. Price pr kWh: {price}", level = 'INFO')
            for r in RECIPIENTS:
                self.ADapi.notify(f"Starting appliance at {startWashingAt}", title = "Appliances", name = r)
        else:
            self.ADapi.run_in(self.startWashing, 10)


    def startWashing(self, kwargs) -> None:
        if (
            self.ADapi.get_state(self.program) == 'off'
            and self.ADapi.get_state(self.remote_start) == 'on'
        ):
            self.ADapi.turn_on(self.program)


    def resetHandler(self) -> None:
        if self.handler != None:
            if self.ADapi.timer_running(self.handler):
                try:
                    self.ADapi.cancel_timer(self.handler)
                except Exception as e:
                    self.ADapi.log(f"Not possible to stop timer for appliance. {e}", level = 'DEBUG')
                finally:
                    self.handler = None
                    