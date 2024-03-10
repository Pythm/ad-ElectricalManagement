""" ElectricalManagement.

    @Pythm / https://github.com/Pythm

"""

__version__ = "0.0.1"

import hassapi as hass
import datetime
import math
import json
import csv

RECIPIENTS:list = []
JSON_PATH:str = ''
OUT_TEMP:float = 0.0
RAIN_AMOUNT:float = 0.0
WIND_AMOUNT:float = 0.0

class ElectricityPrice:

    ADapi = None
    nordpool_prices = None
    currency = None
    daytax = 0
    nighttax = 0
    workday = None
    elpricestoday = []
    nordpool_todays_prices = []
    nordpool_tomorrow_prices = []
    sorted_elprices_today = []
    sorted_elprices_tomorrow = []


    def __init__(self,
        api,
        nordpool = None,
        daytax = None,
        nighttax = None,
        workday = None,
        power_support_above = 10,
        support_amount = 0
    ):

        self.ADapi = api
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

        self.currency = self.ADapi.get_state(entity_id = self.nordpool_prices, attribute = 'currency')
        if daytax:
            self.daytax = daytax
        if nighttax:
            self.nighttax = nighttax
        if workday:
            self.workday = workday
        else:
            self.workday = 'binary_sensor.workday_sensor'
            if not self.ADapi.entity_exists(self.ADapi.get_entity(self.workday)):
                self.ADapi.set_state(self.workday, state = 'on')
                self.ADapi.log(
                    "'workday' binary_sensor not defined in app configuration. Will only use Saturdays and Sundays as nighttax and not Holidays. "
                    "https://www.home-assistant.io/integrations/workday/",
                    level = 'INFO'
                )

        self.power_support_above = power_support_above
        self.support_amount = support_amount

        self.getprices()
        self.ADapi.listen_state(self.update_price_rundaily, self.nordpool_prices,
            attribute = 'tomorrow'
        )


    def update_price_rundaily(self, entity, attribute, old, new, kwargs):
        self.getprices()


        # Fetches prices from Nordpool sensor and adds day and night tax
    def getprices(self):
        self.elpricestoday = []
        isNotWorkday = self.ADapi.get_state(self.workday) == 'off'


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
                self.ADapi.log(f"Nordpool prices tomorrow failed. Exception: {e}", level = 'DEBUG')
                self.ADapi.log(self.elpricestoday)
                self.sorted_elprices_tomorrow = []
            else:
                self.sorted_elprices_tomorrow = sorted(self.sorted_elprices_tomorrow)


        # Returns starttime, endtime and price for cheapest continuous hours with different ranges depenting on time the call was made
    def getContinuousCheapestTime(self,
        hoursTotal = 1,
        calculateBeforeNextDayPrices = False,
        startTime = datetime.datetime.today().hour,
        finishByHour = 8
    ):
        finishByHour += 1
        h = math.floor(hoursTotal)
        if h == 0:
            h = 1
        if (
            self.ADapi.now_is_between('13:00:00', '23:59:59')
            and len(self.elpricestoday) >= 47
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
            # TODO: Reload integration based on?:
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
            self.ADapi.log(f"PriceToComplete hour avgPriceToComplete = {avgPriceToComplete}", level = 'INFO') ###

        if startTime < datetime.datetime.today().hour:
            self.ADapi.log(
                f"DEBUG: Starttime {startTime} before adding {datetime.datetime.today().replace(hour = 0, minute = 0, second = 0, microsecond = 0 ) + datetime.timedelta(hours = startTime)}",
                level = 'INFO'
            ) ###
            self.ADapi.log(f"{hoursTotal} - {calculateBeforeNextDayPrices} {finishByHour}", level = 'INFO') ###
            startTime += 24
            self.ADapi.log(
                f"Starttime {startTime} after adding {datetime.datetime.today().replace(hour = 0, minute = 0, second = 0, microsecond = 0 ) + datetime.timedelta(hours = startTime)}",
                level = 'INFO'
            ) ###
        runtime = datetime.datetime.today().replace(hour = 0, minute = 0, second = 0, microsecond = 0 ) + datetime.timedelta(hours = startTime)
        endtime = runtime + datetime.timedelta(hours = hoursTotal)
        if runtime.hour == datetime.datetime.today().hour:
            runtime = datetime.datetime.today().replace(second=0, microsecond=0)
        return runtime, endtime, round(avgPriceToComplete/h, 3)


        # Compares the X hour lowest price to a minimum change and retuns the lowest price
    def findlowprices(self,
        checkhour = 1,
        hours = 6,
        min_change = 0.1
    ):
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


        # Finds peak variations in electricity price for saving purposes and returns list with datetime objects
    def findpeakhours(self,
        peakdifference = 0.3,
        max_continuous_hours = 3,
        on_for_minimum = 6
    ):
        peak_hours = []
        hour = 0
        length = len(self.elpricestoday) -1
        while hour < length:
                # Checks if price drops more than wanted peak difference
            if self.elpricestoday[hour] - self.elpricestoday[hour+1] >= peakdifference:
                if self.elpricestoday[hour] > self.findlowprices(checkhour = hour, hours = on_for_minimum):
                    peak_hours.append(hour)
                else:
                    countDown = on_for_minimum
                    h = hour +1
                    while (
                        self.elpricestoday[hour] - self.elpricestoday[h] >= peakdifference
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
                    self.elpricestoday[hour] - self.elpricestoday[hour+3] >= peakdifference * 1.8
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
                peakdiff = peakdifference
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


        # Finds low price variations in electricity price for spending purposes and returns list with datetime objects
    def findLowPriceHours(self,
        peakdifference = 0.6,
        max_continuous_hours = 2
    ):
        cheap_hours = []
        hour = 1
        length = len(self.elpricestoday) -2

        while hour < length:
                # Checks if price increases more than wanted peak difference
            if (
                self.elpricestoday[hour+1] - self.elpricestoday[hour] >= peakdifference
                and self.elpricestoday[hour] <= self.findlowprices(hour, 3, 0.08)
            ):
                cheap_hours.append(hour)
                if self.elpricestoday[hour-1] < self.elpricestoday[hour] + 0.06:
                    cheap_hours.append(hour-1)
                hour += 1
                # Checks if price increases x1,4 peak difference during two hours
            elif (
                self.elpricestoday[hour+1] - self.elpricestoday[hour] >= (peakdifference * 0.6)
                and self.elpricestoday[hour+1] - self.elpricestoday[hour-1] >= (peakdifference * 1.4)
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

        # Returns how many hours continiously peak hours turn something off/down for savings
    def continuousHoursOff(self, peak_hours = []):
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


        # Formats hours list to readable string for easy logging/testing of settings
    def print_peaks(self, peak_hours = []):
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



""" ElectricalUsage.
Main class of ElectricalManagement

    @Pythm / https://github.com/Pythm

"""
class ElectricalUsage(hass.Hass):

    chargers:list = []
    appliances:list = []
    heaters:list = []


    def initialize(self):

        self.log("electricalManagement initialized") ###

        global RECIPIENTS
        RECIPIENTS = self.args.get('notify_receiver', [])

        global ELECTRICITYPRICE
        ELECTRICITYPRICE = ElectricityPrice(self,
        nordpool = self.args.get('nordpool',None),
        daytax = self.args.get('daytax',None),
        nighttax = self.args.get('nighttax',None),
        workday = self.args.get('workday',None),
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
                "Check out https://tibber.com/no to learn more. "
                "If you are interested in switchin to Tibber please use my invite link to get a startup bonus: "
                "https://invite.tibber.com/fydzcu9t",
                level = 'INFO'
            )
            raise Exception (
                "accumulated_consumption_current_hour not found. Please install Tibber or input equivialent to provide needed data"
            )
        else:
            attr_last_updated = self.get_state(entity_id=self.accumulated_consumption_current_hour, attribute="last_updated")
            if not attr_last_updated:
                self.log(
                    f"{self.get_state(self.accumulated_consumption_current_hour)} has no 'last_updated' attribute. Function might fail",
                    level = 'WARNING'
                )

            # Production sensors
        self.current_production = self.args.get('power_production', None) # Watt
        self.accumulated_production_current_hour = self.args.get('accumulated_production_current_hour', None) # Watt

            # Setting buffer for kWh usage
        self.buffer = self.args.get('buffer', 0.5)
        self.buffer -= 0.04 # Correction of calculation
        self.max_kwh_goal: int = self.args.get('max_kwh_goal', 5)


            # Establish and recall persistent data using JSON
        """ Persistent data will be updated with max kWh usage for the 3 highest hours.
            In Norway we pay extra for average of the 3 highest peak loads in steps 2-5kWh - 5-10kWh etc.

            Store max Ampere the car can charge when connected to Easee in cases where the set ampere in charger
            is higher that the car can receive

            Store consuption after save functions depending on outside temperature and hours saving,
            to better calculate how many hours cars need to finish charging.
        """
        global JSON_PATH
        JSON_PATH = self.args.get('json_path', '/conf/apps/ElectricalManagement/ElectricityData.json')
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

        self.max_kwh_usage_pr_hour = ElectricityData['MaxUsage']['max_kwh_usage_pr_hour']
        newTotal:float = 0.0
        self.top_usage_hour = ElectricityData['MaxUsage']['topUsage'][0]


            # Default vacation state for saving purposes when away from home for longer periodes
        if 'away_state' in self.args:
            self.away_state = self.args['away_state']
        else:
            self.away_state = 'input_boolean.vacation'
            if not self.entity_exists(self.get_entity(self.away_state)):
                self.set_state(self.away_state, state = 'off')
            else:
                self.log(
                    "'away_state' not configured. Using 'input_boolean.vacation' as default away state",
                    level = 'WARNING'
                )


            # Weather sensors
        global RAIN_AMOUNT
        global WIND_AMOUNT

        self.weather_temperature = None
        self.outside_temperature = self.args.get('outside_temperature', None)
        self.rain_sensor = self.args.get('rain_sensor', None)
        self.rain_level = self.args.get('rain_level',3)
        self.anemometer = self.args.get('anemometer', None)
        self.anemometer_speed = self.args.get('anemometer_speed',40)
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
        global CHARGE_SCHEDULER
        CHARGE_SCHEDULER = Scheduler(self)

        self.queueChargingList:list = [] # Cars/chargers currently charging.
        self.solarChargingList:list = [] # Cars/chargers currently charging.

        teslas = self.args.get('tesla', {})
        for t in teslas:

            tesla = Tesla(self,
                charger = t.get('charger',None),
                charger_sensor = t.get('charger_sensor',None),
                charger_switch = t.get('charger_switch',None),
                charging_amps = t.get('charging_amps',None),
                charger_power = t.get('charger_power',None),
                charge_limit = t.get('charge_limit',None),
                asleep_sensor = t.get('asleep_sensor', None),
                online_sensor = t.get('online_sensor',None),
                battery_sensor = t.get('battery_sensor',None),
                location_tracker = t.get('location_tracker',None),
                destination_location_tracker = t.get('destination_location_tracker',None),
                arrival_time = t.get('arrival_time',None),
                software_update = t.get('software_update',None),
                force_data_update = t.get('force_data_update', None),
                polling_switch = t.get('polling_switch',None),
                data_last_update_time = t.get('data_last_update_time',None),
                pref_charge_limit = t.get('pref_charge_limit',90),
                charge_on_solar = t.get('charge_on_solar', False),
                battery_size = t.get('battery_size',100),
                namespace = t.get('namespace', None),
                finishByHour = t.get('finishByHour',None),
                priority = t.get('priority',3),
                charge_now = t.get('charge_now',None),
                electric_consumption = t.get('electric_consumption',None),
                departure = t.get('departure',None)
            )
            self.chargers.append(tesla)

        easees = self.args.get('easee', {})
        for e in easees:

            easee = Easee(self,
                charger = e.get('charger',None),
                charger_status = e.get('charger_status',None),
                reason_for_no_current = e.get('reason_for_no_current',None),
                current = e.get('current',None),
                charger_power = e.get('charger_power',None),
                voltage = e.get('voltage',None),
                max_charger_limit = e.get('max_charger_limit',None),
                online_sensor = e.get('online_sensor',None),
                session_energy = e.get('session_energy',None),
                battery_size = e.get('battery_size',None),
                namespace = e.get('namespace', None),
                finishByHour = e.get('finishByHour',None),
                priority = e.get('priority',3),
                charge_now = e.get('charge_now',None),
                pref_charge_limit = 100,
                charge_on_solar = t.get('charge_on_solar', False),
                electric_consumption = e.get('electric_consumption',None),
                departure = e.get('departure',None),
                guest = e.get('guest',None)
            )
            self.chargers.append(easee)


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
                                f"No external indoor temperature sensor provided as 'indoor_sensor_temp'. "
                                f"Using built in sensor {heater['indoor_sensor_temp']}",
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

            if not 'away_state' in heater:
                heater['away_state'] = self.away_state

            climate = Climate(self,
                heater = heater['heater'],
                consumptionSensor = heater['consumptionSensor'],
                kWhconsumptionSensor = heater['kWhconsumptionSensor'],
                max_continuous_hours = heater.get('max_continuous_hours', 2),
                on_for_minimum = heater.get('on_for_minimum', 12),
                peakdifference = heater.get('peakdifference', 1),
                namespace = heater.get('namespace', None),
                away = heater['away_state'],
                automate = heater.get('automate', None),
                recipient = heater.get('recipient', None),
                indoor_sensor_temp = heater.get('indoor_sensor_temp', None),
                target_indoor_temp = heater.get('target_indoor_temp', 23),
                rain_level = heater.get('rain_level', self.rain_level),
                anemometer_speed = heater.get('anemometer_speed', self.anemometer_speed),
                low_price_max_continuous_hours = heater.get('low_price_max_continuous_hours', 2),
                low_price_peakdifference = heater.get('low_price_peakdifference', 1),
                windowsensors = heater.get('windowsensors', []),
                daytime_savings = heater.get('daytime_savings', {}),
                temperatures = heater.get('temperatures', {})
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
            if not 'peakdifference' in heater_switch:
                heater_switch['peakdifference'] = 0.3
            if not 'away_state' in heater_switch:
                heater_switch['away_state'] = self.away_state


            on_off_switch = On_off_switch(self,
                heater = heater_switch['switch'],
                consumptionSensor = heater_switch['consumptionSensor'],
                kWhconsumptionSensor = heater_switch['kWhconsumptionSensor'],
                max_continuous_hours = heater_switch['max_continuous_hours'],
                on_for_minimum = heater_switch['on_for_minimum'],
                peakdifference = heater_switch['peakdifference'],
                namespace = heater_switch.get('namespace', None),
                away = heater_switch['away_state'],
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
        self.SolarProducing_ChangeToZero = False

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
    def electricityprices_updated(self, entity, attribute, old, new, kwargs):
        for heater in self.heaters:
            self.run_in(heater.heater_getNewPrices, 1)

        if len(new) > 0:
            self.run_in(self.findConsumptionAfterTurnedBackOn, 10)
            self.run_in(self.calculateIdleConsumption, 20)

            for c in self.chargers:
                self.run_in(c.findNewChargeTimeWhenTomorrowPricesIsReady, 30)


    def checkElectricalUsage(self, kwargs):
        """ Calculate and ajust consumption to stay within kWh limit
            Start charging when time to charge
        """
        global CHARGE_SCHEDULER
        global OUT_TEMP

        accumulated_kWh = self.get_state(self.accumulated_consumption_current_hour)
        current_consumption = self.get_state(self.current_consumption)

        runtime = datetime.datetime.now()
        remaining_minute = 60 - int(runtime.minute)

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
                    c.getLocation() == 'home'
                    and c.getChargingState() == 'Charging'
                ):
                    current_consumption += float(self.get_state(c.charging_amps)) * c.voltphase
            self.log(f"Current Unavailable. Estimate: {current_consumption}", level = 'INFO') ###

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
            self.log(
                f"Accumulated unavailable. Estimate: {accumulated_kWh}. "
                f"Added {round((current_consumption/60000),3)} to {self.last_accumulated_kWh}",
                level = 'INFO'
            )
            self.last_accumulated_kWh = accumulated_kWh

        else:
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
                or not self.solarChargingList
            ):
                self.SolarProducing_ChangeToZero = False
                for c in self.chargers:
                    if (
                        c.getLocation() == 'home'
                        and c.getChargingState() == 'Charging'
                    ):
                        if (
                            (c.priority == 1 or c.priority == 2)
                            and CHARGE_SCHEDULER.isPastChargingTime()
                        ):
                            pass

                        elif not c.dontStopMeNow():
                            c.stopCharging()
                            if CHARGE_SCHEDULER.isPastChargingTime():
                                self.log(
                                    f"Was not able to finish charging {c.charger} "
                                    f"with {c.kWhRemaining()} kWh remaining before prices increased.",
                                    level = 'INFO'
                                )

            self.heatersRedusedConsumption = []


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
            #        if c.getLocation() == 'home':
            #            c.wakeMeUp()
            if available_Wh < -2000:
                self.findCharingNotInQueue()
                self.log(f"Available watt is {available_Wh}. Finding chargers not in queue", level="INFO") ###


            if self.queueChargingList:
                reduce_Wh = available_Wh
                if self.heatersRedusedConsumption:
                    for heater in self.heatersRedusedConsumption:
                        reduce_Wh -= heater.prev_consumption

                for queue_id in reversed(self.queueChargingList):
                    for c in self.chargers:
                        if (
                            c.vehicle_id == queue_id
                            and reduce_Wh < 0
                        ):

                            if c.ampereCharging == 0:
                                c.ampereCharging = math.ceil(float(self.get_state(c.charging_amps)))

                            if c.ampereCharging > 6:
                                AmpereToReduce = math.ceil(reduce_Wh / c.voltphase)
                                self.log(f"Ampere to reduce in reducing overconsumption: {AmpereToReduce}") ###
                                if (c.ampereCharging + AmpereToReduce) < 6:
                                    c.setChargingAmps(charging_amp_set = 6)
                                    available_Wh += (AmpereToReduce + 6) * c.voltphase
                                    reduce_Wh += (AmpereToReduce + 6) * c.voltphase
                                    self.log(f"Available watt after reducing charging speed to 6amp: {available_Wh}", level = 'INFO') ###
                                else:
                                    c.changeChargingAmps(charging_amp_change = AmpereToReduce)
                                    available_Wh += AmpereToReduce * c.voltphase
                                    reduce_Wh += AmpereToReduce * c.voltphase
                                    break


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
            ReduceCharging = 0
            #for c in self.chargers:
            #    if c.getLocation() == 'home':
            #        c.wakeMeUp()
            self.findCharingNotInQueue()

            for heater in reversed(self.heatersRedusedConsumption):
                if heater.prev_consumption < available_Wh:
                    heater.setPreviousState()
                    available_Wh -= heater.prev_consumption
                    self.heatersRedusedConsumption.remove(heater)
                else:
                    ReduceCharging -= heater.prev_consumption
            
            if (
                self.queueChargingList
                and ReduceCharging > 0
            ):
                self.log(f"Reduce charging: {ReduceCharging} with added available: {ReduceCharging + available_Wh}", level = 'INFO') ###
                ReduceCharging += available_Wh

                for queue_id in reversed(self.queueChargingList):
                    for c in self.chargers:
                        if (
                            c.vehicle_id == queue_id
                            and ReduceCharging < 0
                        ):

                            if c.ampereCharging == 0:
                                c.ampereCharging = math.ceil(float(self.get_state(c.charging_amps)))

                            if c.ampereCharging > 6:
                                AmpereToReduce = math.ceil(ReduceCharging / c.voltphase)
                                if (c.ampereCharging + AmpereToReduce) < 6:
                                    c.setChargingAmps(charging_amp_set = 6)
                                    available_Wh += (AmpereToReduce - 6) * c.voltphase
                                    ReduceCharging += (AmpereToReduce - 6) * c.voltphase
                                    self.log(f"Available watt after reducing charging speed to 6amp: {available_Wh}", level = 'INFO') ###
                                else:
                                    c.changeChargingAmps(charging_amp_change = AmpereToReduce)
                                    available_Wh += AmpereToReduce * c.voltphase
                                    ReduceCharging += AmpereToReduce * c.voltphase
                                    break


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

            #self.log(f"Production is higher than consumption. Increasing usage. {accumulated_kWh} <= {production_kWh}", level="INFO") ###
            #self.log(f"projected_kWh_usage: {projected_kWh_usage}", level="INFO") ###
            #self.log(f"Current consumption: {current_consumption} - production: {current_production} = {available_Wh}", level="INFO") ###

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
                    if c.getLocation() == 'home':
                        if c.getChargingState() == 'Charging':
                            c.charging_on_solar = True
                            self.solarChargingList.append(c.vehicle_id)
                        elif (
                            c.getChargingState() == 'Stopped'
                            and c.state_of_charge() < c.pref_charge_limit
                            and available_Wh > 1600
                        ):
                            c.startCharging()
                            c.charging_on_solar = True
                            self.solarChargingList.append(c.vehicle_id)
                            AmpereToCharge = math.ceil(available_Wh / c.voltphase)
                            c.setChargingAmps(charging_amp_set = AmpereToCharge)
                            return

                # Check if any is below prefered charging limit
                for c in self.chargers:
                    if c.getLocation() == 'home':
                        if c.getChargingState() == 'Charging':
                            self.solarChargingList.append(c.vehicle_id)
                            c.charging_on_solar = True
                        elif (
                            c.pref_charge_limit > c.oldChargeLimit
                        ):
                            c.charging_on_solar = True
                            c.changeChargeLimit(c.pref_charge_limit)
                            c.startCharging()
                            self.solarChargingList.append(c.vehicle_id)
                            AmpereToCharge = math.ceil(available_Wh / c.voltphase)
                            c.setChargingAmps(charging_amp_set = AmpereToCharge)
                            return

                pass
            else :
                for queue_id in self.solarChargingList:
                    for c in self.chargers:
                        if c.vehicle_id == queue_id:
                            if c.getChargingState() == 'Charging':
                                AmpereToIncrease = math.ceil(available_Wh / c.voltphase)
                                c.changeChargingAmps(charging_amp_change = AmpereToIncrease)
                                return
                            elif (
                                c.getChargingState() == 'Complete'
                                and c.state_of_charge() >= c.pref_charge_limit
                            ):
                                c.charging_on_solar = False
                                c.changeChargeLimit(c.oldChargeLimit)
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

        
            #self.log(f"Production is lower than consumption. Increasing usage. {accumulated_kWh} > {production_kWh}", level="INFO") ###
            #self.log(f"projected_kWh_usage: {projected_kWh_usage}", level="INFO") ###
            #self.log(f"Current production : {current_production} - consumption: {current_consumption} = {available_Wh}", level="INFO") ###

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
                    if c.vehicle_id == queue_id:

                        if c.ampereCharging == 0:
                            c.ampereCharging = math.floor(float(self.get_state(c.charging_amps)))

                        if c.ampereCharging > 6:
                            AmpereToReduce = math.floor(available_Wh / c.voltphase)
                            if (c.ampereCharging + AmpereToReduce) < 6:
                                c.setChargingAmps(charging_amp_set = 6)
                                available_Wh += (AmpereToReduce - 6) * c.voltphase
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
                        if c.vehicle_id == queue_id:
                            c.charging_on_solar = False
                            c.changeChargeLimit(c.oldChargeLimit)
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
                vehicle_id = None
                
                if self.queueChargingList:

                    for queue_id in self.queueChargingList:
                        for c in self.chargers:
                            if c.vehicle_id == queue_id:
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
                                            self.log(f"{c.charger} charging at Verstappen speed. Search for next charger to start", level = 'INFO') ###
                                            vehicle_id = CHARGE_SCHEDULER.findChargerToStart()
                                            if c.vehicle_id == vehicle_id:
                                                vehicle_id = CHARGE_SCHEDULER.findNextChargerToStart()

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
                        if vehicle_id == None:
                            vehicle_id = CHARGE_SCHEDULER.findChargerToStart()

                if vehicle_id != None:
                    for c in self.chargers:
                        if c.vehicle_id == vehicle_id:
                            if c.vehicle_id not in self.queueChargingList:
                                c.startCharging()
                                self.queueChargingList.append(c.vehicle_id)
                                AmpereToCharge = math.floor(available_Wh / c.voltphase)
                                c.setChargingAmps(charging_amp_set = AmpereToCharge)
                                return



        # Finds charger not started from queue.
    def findCharingNotInQueue(self):
        softwareUpdates = False
        for c in self.chargers:
            if c.getLocation() == 'home':
                if c.SoftwareUpdates():
                    softwareUpdates = True
        # Stop other chargers if a car is updating software. Not able to adjust chargespeed when updating.
        if softwareUpdates:
            for c in self.chargers:
                if (
                    c.getLocation() == 'home'
                    and not c.dontStopMeNow()
                    and c.getChargingState() == 'Charging'
                ):
                    c.stopCharging()
            return False

        for c in self.chargers:
            if (
                c.getLocation() == 'home'
                and c.getChargingState() == 'Charging'
                and c.vehicle_id not in self.queueChargingList
                and not self.SolarProducing_ChangeToZero
            ):
                self.queueChargingList.append(c.vehicle_id)
        return True


    def chargerToForceUpdate(self):
        """ A function to force update of Teslas charging when no cars charging but power to chargers is measured.
            TODO: 
            Add a listen state in 'initialize' to listen to a power sensor.
            Find a good logic to not force update unless needed.
            Two or more cars connected/linked to same power sensor. 
            FIND sensor ampere charging and test speed/charging
        """

        chargerToForceUpdate:list = []
        electricChargeConsumption:float = 0.0
        consuptionTest:str = ""
        for c in self.chargers: # Change to cars charging thru power sensor
            try:
                cConsump = float(self.get_state(c.electric_consumption))
            except ValueError as ve:
                self.log(f"Not able to get consumption sensor {self.get_state(c.electric_consumption)} ValueError: {ve}", level = 'WARNING')
                cConsump = 0
            except Exception as e:
                self.log(f"Not able to get consumption sensor {self.get_state(c.electric_consumption)} Exception: {e}", level = 'WARNING')
                cConsump = 0
            if cConsump > 100: 
                if c.getLocation() == 'home' or c.getLocation() == 'away':

                    # Charging
                        # Sjekk om consumption og charging er nogenlunde likt -> Break / .pop()
                    if cConsump > (c.ampereCharging * c.voltphase) - 1000 and cConsump -1000 < (c.ampereCharging * c.voltphase):
                        if consuptionTest == c.electric_consumption:
                            if chargerToForceUpdate:
                                poop = chargerToForceUpdate.pop()
                                self.log(f"Pop {poop} from beeing updated because {c.charger} is charging.", level = 'INFO') ###
                        if c.getChargingState() != 'Charging':
                            chargerToForceUpdate.append(c.vehicle_id)
                            self.log(
                                f"Append {c.vehicle_id}. {c.charger} is {c.getChargingState()}. "
                                f"Charging: {c.ampereCharging * c.voltphase} and is close enough to electric_consumption {cConsump}",
                                level = 'INFO'
                            ) ###
                        consuptionTest = c.electric_consumption # Get name of measure entity in case more chargers are charging on same
                    # Other car charging
                    elif c.ampereCharging > 0:
                        consuptionTest = c.electric_consumption # Get name of measure entity in case more chargers are charging on same
                        if c.getChargingState() != 'Charging':
                            chargerToForceUpdate.append(c.vehicle_id)
                            self.log(
                                f"Append {c.vehicle_id} = {c.charger} to be updated. "
                                f"State: {c.getChargingState()}. Charging {c.ampereCharging * c.voltphase} with electric_consumption {cConsump}",
                                level = 'INFO'
                            ) ###
                    elif c.getChargingState() == 'Charging' and c.ampereCharging == 0:
                        chargerToForceUpdate.append(c.vehicle_id)
                        self.log(f"{c.charger} has Charging state with Ampere = 0. Finished or started? electric_consumption {cConsump}", level = 'INFO') ###
                    elif consuptionTest != c.electric_consumption:
                        chargerToForceUpdate.append(c.vehicle_id)
                        consuptionTest = c.electric_consumption
                        self.log(
                            f"Append {c.vehicle_id} = {c.charger} to be updated. "
                            f"State: {c.getChargingState()}. consuptionTest != c.electric_consumption",
                            level = 'INFO'
                        ) ###
                    if c.getLocation() == 'away':
                        self.log(f"{c.charger} is away...", level = 'INFO') ###
                
        
        if chargerToForceUpdate:
            self.log(f"Chargers to update: {chargerToForceUpdate}", level = 'INFO') ###

        #for c in self.chargers:
        #    if c.vehicle_id in chargerToForceUpdate:
        #        self.log(f"Do data pull from {c.charger}. {c.getChargingState()}") ###
        #        c.forceDataUpdate()
        #        self.log(f"After data pull from {c.charger}. {c.getChargingState()}") ###
        # TODO: Add to updated list and check if time since last > 10 min.


        """ Functions to calculate and log consumption based on outside temperature
            to better be able to calculate chargingtime based on max kW pr hour usage
        """
    def findConsumptionAfterTurnedBackOn(self, kwargs):
        global ELECTRICITYPRICE
        for heater in self.heaters:
            heater.off_for_hours, turnsBackOn = ELECTRICITYPRICE.continuousHoursOff(peak_hours = heater.time_to_save)
            for daytime in heater.daytime_savings:
                if 'start' in daytime and 'stop' in daytime:
                    if not 'presence' in daytime:
                        off_hours = self.parse_datetime(daytime['stop']) - self.parse_datetime(daytime['start'])
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


    def calculateIdleConsumption(self, kwargs):
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


    def logIdleConsumption(self):
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
    def logHighUsage(self):
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
    def resetHighUsage(self):
        global JSON_PATH
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        self.max_kwh_usage_pr_hour = self.max_kwh_goal
        ElectricityData['MaxUsage']['max_kwh_usage_pr_hour'] = self.max_kwh_usage_pr_hour
        ElectricityData['MaxUsage']['topUsage'] = [0,0,float(self.get_state(self.accumulated_consumption_current_hour))]

        with open(JSON_PATH, 'w') as json_write:
            json.dump(ElectricityData, json_write, indent = 4)



        # Weather handling
    def outsideTemperatureUpdated(self, entity, attribute, old, new, kwargs):
        global OUT_TEMP
        try:
            OUT_TEMP = float(new)
        except ValueError as ve:
            self.log(f"Not able to set new outdoor temperature: {new}. {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Not able to set new outdoor temperature: {new}. {e}", level = 'INFO')

    def outsideBackupTemperatureUpdated(self, entity, attribute, old, new, kwargs):
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

    def rainSensorUpdated(self, entity, attribute, old, new, kwargs):
        global RAIN_AMOUNT
        try:
            RAIN_AMOUNT = float(new)
        except ValueError as ve:
            self.log(f"Not able to set new rain amount {new} ValueError: {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Not able to set new rain amount {new} Exception: {e}", level = 'INFO')

    def anemometerUpdated(self, entity, attribute, old, new, kwargs):
        global WIND_AMOUNT
        try:
            WIND_AMOUNT = float(new)
        except ValueError as ve:
            self.log(f"Not able to set new wind amount {new} ValueError: {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Not able to set new wind amount {new} Exeption: {e}", level = 'INFO')


    def mode_event(self, event_name, data, kwargs):
        """ Listens to same mode event that I have used in Lightwand: https://github.com/Pythm/ad-Lightwand
            If mode name equals 'fire' it will turn off all charging and heating.
            To call from another app use: self.fire_event("MODE_CHANGE", mode = 'fire')
        """
        if data['mode'] == 'fire':
            for c in self.chargers:
                if (
                    c.getLocation() == 'home'
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

    def __init__(self, api):
        self.ADapi = api

        # Helpers
        self.chargingQueue:list = []
        self.chargingStart = None
        self.chargingStop = None
        self.price:float = 0.0
        self.informedStart = None
        self.informedStop = None
       
        # Is updated from main class when turning off/down to save on electricity price
        self.turnsBackOn:int = 22
        self.availableWatt:list = []


    def calculateChargingTimes(self, kWhRemaining, totalW_AllChargers):
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
            level = 'WARNING'
        )
        return math.ceil(kWhRemaining / (totalW_AllChargers / 1000))


        """ Helpers used to return data
        """
    def isChargingTime(self):
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

    def isPastChargingTime(self):
        if self.chargingStop == None:
            return True
        elif datetime.datetime.today() > self.chargingStop:
            return True
        return False

    def hasChargingScheduled(self, vehicle_id):
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                return True
        return False

    def findChargerToStart(self):
        if self.isChargingTime():
            pri = 1
            while pri < 5:
                for c in self.chargingQueue:
                    if c['priority'] == pri:
                        return c['vehicle_id']
                pri += 1
        return None

    def findNextChargerToStart(self):
        if self.isChargingTime():
            foundFirst = False
            pri = 1
            while pri < 5:
                for c in self.chargingQueue:
                    if c['priority'] == pri:
                        if not foundFirst:
                            foundFirst = True
                        else:
                            return c['vehicle_id']
                pri += 1
        return None


        # Removes a charger from queue after finished charging or disconnected.
    def removeFromQueue(self, vehicle_id):
        for c in self.chargingQueue:
            if vehicle_id == c['vehicle_id']:
                self.chargingQueue.remove(c)
        if len(self.chargingQueue) == 0:
            self.chargingStart = None
            self.chargingStop = None
            self.informedStart = None
            self.informedStop = None


    def queueForCharging(self,
        vehicle_id,
        kWhRemaining,
        maxAmps,
        voltphase,
        finishByHour,
        priority
    ):
        """ Adds charger to queue and sets charging time
        """
        global RECIPIENTS
        global ELECTRICITYPRICE

        if kWhRemaining <= 0:
            self.removeFromQueue(vehicle_id = vehicle_id)
            return False

        if self.hasChargingScheduled(vehicle_id):
            for c in self.chargingQueue:
                if vehicle_id == c['vehicle_id']:
                    if c['kWhRemaining'] == kWhRemaining:
                        if self.isChargingTime():
                            return True
                        return False
                    else:
                        c['kWhRemaining'] = kWhRemaining
                        c['finishByHour'] = finishByHour
        else:
            self.chargingQueue.append({'vehicle_id' : vehicle_id,
                'kWhRemaining' : kWhRemaining,
                'maxAmps' : maxAmps,
                'voltphase' : voltphase,
                'finishByHour' : finishByHour,
                'priority' : priority})

        # Finds low price during day awaiting tomorrows prices
        if (
            self.ADapi.now_is_between('07:00:00', '14:00:00')
            and len(ELECTRICITYPRICE.elpricestoday) == 24
        ):
            """ TODO: A better logic to charge if price is lower than usual before tomorrow prices is available from Nordpool.
            for c in self.chargingQueue:
                kWhToCharge += c['kWhRemaining']
            hoursToCharge = self.calculateChargingTimes(kWhRemaining = kWhToCharge, totalW_AllChargers = totalW_AllChargers)
            
            Check against hours until 14:00 and use smallest value hour to find lowest price to charge
            """
            self.price = ELECTRICITYPRICE.sorted_elprices_today[1] # Set price to the second lowest hour and charge if price is equal or lower.
            self.ADapi.log(
                f"Wait for tomorrows prices before setting chargetime for {vehicle_id}. Charge if price is lower than {self.price}",
                level = 'INFO'
            ) ###
            return self.isChargingTime()

        self.chargingStart = None
        self.chargingStop = None
        kWhToCharge:float = 0.0
        totalW_AllChargers = 0.0
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
                        f"{c['vehicle_id']} Could not get availableWatt. Using maxAmp * voltage = {estHourCharge} estimated hours charge",
                        level = 'INFO'
                    )
                except Exception as e:
                    self.ADapi.log(f"{c['vehicle_id']} Could not get availableWatt. Exception: {e}", level = 'WARNING')

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

        self.chargingStart, self.chargingStop, self.price = ELECTRICITYPRICE.getContinuousCheapestTime(
            hoursTotal = hoursToCharge,
            calculateBeforeNextDayPrices = False,
            startTime = datetime.datetime.today().hour,
            finishByHour = finishByHour
        )

        if (
            self.chargingStart != None
            and self.chargingStop != None
        ):

            self.ADapi.log(
                f"chargingStart {self.chargingStart}. chargingStop {self.chargingStop}",
                level = 'INFO'
            )

            self.chargingStart, self.chargingStop = self.findChargingTime(
                ChargingAt = self.chargingStart,
                EndAt = self.chargingStop,
                price = self.price
            )

            # Notify chargetime
            if (
                self.chargingStart != self.informedStart
                or self.chargingStop != self.informedStop
            ):
                self.informedStart = self.chargingStart
                self.informedStop = self.chargingStop
                for r in RECIPIENTS:
                    self.ADapi.notify(
                        f"Start charge at {self.chargingStart}. Stopp at {self.chargingStop}",
                        title = "🔋 Charge Queue",
                        name = r
                    )
            
            if self.isChargingTime():
                return True
        return False


    @staticmethod
    def findChargingTime(ChargingAt, EndAt, price):
        global ELECTRICITYPRICE
        EndChargingHour = EndAt.hour
        if EndAt.day - 1 == datetime.datetime.today().day:
            EndChargingHour += 24

        while (
            EndChargingHour < len(ELECTRICITYPRICE.elpricestoday) -1
            and price + 0.3 > ELECTRICITYPRICE.elpricestoday[EndChargingHour]
        ):
            EndChargingHour += 1
            EndAt += datetime.timedelta(hours = 1)

        EndAt = EndAt.replace(minute = 0, second = 0, microsecond = 0)

        StartChargingHour = ChargingAt.hour
        if ChargingAt.day - 1 == datetime.datetime.today().day:
            StartChargingHour += 24
        startHourPrice = ELECTRICITYPRICE.elpricestoday[StartChargingHour]
        if (
            price < startHourPrice - 0.5
            and startHourPrice < ELECTRICITYPRICE.elpricestoday[StartChargingHour+1] -0.4
        ):
            ChargingAt += datetime.timedelta(hours = 1)
        else:
            hoursToChargeStart = ChargingAt - datetime.datetime.today().replace(second = 0, microsecond = 0)
            hoursToStart = hoursToChargeStart.seconds//3600

            while (
                hoursToStart > 0
                and startHourPrice + 0.01 >= ELECTRICITYPRICE.elpricestoday[StartChargingHour-1]
                and price + 0.02 >= ELECTRICITYPRICE.elpricestoday[StartChargingHour-1]
            ):
                StartChargingHour -= 1
                hoursToStart -= 1
                ChargingAt -= datetime.timedelta(hours = 1)

        return ChargingAt, EndAt



        """ FIXME:
            Continue from here!
        """


class Charger:
    """ Charger
        Parent class for charging management
    """

    def __init__(self,
        battery_size = 100, # User input size of battery. Used to calculate amount of time to charge
        namespace = None,
        finishByHour = None, # HA input_number for when car should be finished charging
        priority = 3, # Priority. See full description
        charge_now = None, # HA input_boolean to bypass smartcharge if true
        pref_charge_limit = 100,
        charge_on_solar = False,
        electric_consumption = None, # Sensor with watt consumption
        departure = None
    ):

        self.battery_size = battery_size
        self.priority = priority
        if self.priority > 5:
            self.priority = 5
        self.electric_consumption = electric_consumption

        self.pref_charge_limit = pref_charge_limit
        self.charge_on_solar = charge_on_solar
        self.charging_on_solar = False

        self.vehicle_id:str = '1'

            # Charging handling
        self.ampereCharging = 0
        self.voltphase = 220

            # Sets time charging should be finished
        self.namespace = namespace
        if not finishByHour:
            self.finishByHour = 7
        else:
            if not self.namespace:
                self.finishByHour = math.ceil(float(self.ADapi.get_state(finishByHour)))
                self.ADapi.listen_state(self.finishByHourListen, finishByHour)
            else:
                self.finishByHour = math.ceil(float(self.ADapi.get_state(finishByHour,
                    namespace = self.namespace))
                )
                self.ADapi.listen_state(self.finishByHourListen, finishByHour,
                    namespace = self.namespace
                )
            
            # Possibility to have a input_boolean to disable smart charging until finished
        if not charge_now:
            self.charge_now = False
        else:
            self.charge_now_HA = charge_now
            if not self.namespace:
                self.charge_now = self.ADapi.get_state(charge_now)  == 'on'
                self.ADapi.listen_state(self.chargeNowListen, charge_now)
            else:
                self.charge_now = self.ADapi.get_state(charge_now, namespace = self.namespace)  == 'on'
                self.ADapi.listen_state(self.chargeNowListen, charge_now,
                    namespace = self.namespace
                )

        self.checkCharging_handler = None

            # TODO Maxrange handling: To be re-written before implementation
            # Set a departure time in a HA datetime sensor for when car is almost finished charging to 100% to have warm battery when departing
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


    def finishByHourListen(self, entity, attribute, old, new, kwargs):
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


    def chargeNowListen(self, entity, attribute, old, new, kwargs):
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


    def whenStartedUp(self, kwargs):
        """ Helper function to find chargetime when initialized from child
        """
        if not self.isAvailable():
            self.wakeMeUp()
        if self.kWhRemaining() > 0:
            if self.findNewChargeTime():
                if self.getChargingState() == 'Charging':
                    self.ampereCharging = math.ceil(float(self.ADapi.get_state(self.charging_amps)))
            else:
                self.stopCharging()


        # Functions for charge times
    def findNewChargeTimeWhenTomorrowPricesIsReady(self, kwargs):
        self.ADapi.log(f"Finding new chargetime for {self.charger}")
        self.findNewChargeTime()


    def findNewChargeTime(self):
        global CHARGE_SCHEDULER
        if (
            self.getLocation() == 'home'
            and self.getChargingState() != 'Complete'
            and self.getChargingState() != 'Disconnected'
            and not self.charging_on_solar
        ):

            return CHARGE_SCHEDULER.queueForCharging(
                vehicle_id = self.vehicle_id,
                kWhRemaining = self.kWhRemaining(),
                maxAmps = self.maxChargingAmps(),
                voltphase = self.voltphase,
                finishByHour = self.finishByHour,
                priority = self.priority
            )


    def hasChargingScheduled(self):
        global CHARGE_SCHEDULER
        return CHARGE_SCHEDULER.hasChargingScheduled(self.vehicle_id)


        # Checks to see if charging can be stopped. For now only applicable for Tesla Class
    def SoftwareUpdates(self):
        return False


        # Returns true if charger should not or can not be stopped
    def dontStopMeNow(self):
        if self.charge_now:
            return True
        return self.SoftwareUpdates()


        # Finds out if charger car is awake and values is available
    def isOnline(self):
        return self.ADapi.get_state(self.online_sensor) == 'on' 


    def wakeMeUp(self):
        pass # For now only applicable for Cars

    
    def recentlyUpdated(self):
        return True


    def forceDataUpdate(self):
        pass # For now only applicable for Cars


    def isAvailable(self):
        if self.isOnline():
            if (
                self.ADapi.get_state(self.charging_amps) != 'unknown'
                and self.ADapi.get_state(self.charging_amps) != 'unavailable'
            ):
                return True
            else:
                self.ADapi.log(
                    f"{self.charger} charging_amps is: {self.ADapi.get_state(self.charging_amps)} when checking if available",
                    level = 'INFO'
                ) ###
        return False


        # Returns values
    def getChargingState(self):
        self.ADapi.log(f"getChargingState not implemented for {self.charger}", level = 'WARNING')
        return None
    

    def getChargerPower(self):
        if self.charger_power:
            try:
                return float(self.ADapi.get_state(self.charger_power))
            except ValueError as ve:
                self.ADapi.log(
                    f"{self.charger} Could not get charger_power: {self.ADapi.get_state(self.charger_power)} ValueError: {ve}",
                    level = 'DEBUG'
                )
                return 0
            except TypeError as te:
                self.ADapi.log(
                    f"{self.charger} Could not get charger_power: {self.ADapi.get_state(self.charger_power)} TypeError: {te}",
                    level = 'DEBUG'
                )
                return 0
            except Exception as e:
                self.ADapi.log(
                    f"{self.charger} Could not get charger_power: {self.ADapi.get_state(self.charger_power)} Exception: {e}",
                    level = 'WARNING'
                )
                return 0
        self.ADapi.log(f"getChargerPower not implemented for {self.charger}", level = 'WARNING')
        return 0


    def maxChargingAmps(self):
        self.ADapi.log(f"maxChargingAmps not implemented for {self.charger}", level = 'WARNING')
        return 32


    def getLocation(self):
        return 'home'


    def isChargingAtMaxAmps(self):
        return self.maxChargingAmps() <= self.ampereCharging


    def kWhRemaining(self):
        return self.battery_size


    def state_of_charge(self):
        # FIXME: Return a proper value
        return 100 


    def changeChargingAmps(self, charging_amp_change = 0):
        """ Function to change ampere charging +/-
        """
        if charging_amp_change != 0:
            if self.ampereCharging == 0:
                self.ampereCharging = math.ceil(float(self.ADapi.get_state(self.charging_amps)))
            new_charging_amp = self.ampereCharging + charging_amp_change
            self.setChargingAmps(charging_amp_set = new_charging_amp)


    def setChargingAmps(self, charging_amp_set = 16):
        """ Function to set ampere charging to received value
        """
        if charging_amp_set >self.maxChargingAmps():
            self.ampereCharging = self.maxChargingAmps()
        elif charging_amp_set < 6:
            self.ampereCharging = 6
        else:
            self.ampereCharging = charging_amp_set
        return self.ampereCharging


    def changeChargeLimit(self, chargeLimit = 90 ):
        pass # For now only applicable for Cars


        # Functions to start / stop charging
    def startCharging(self):
        state:str = self.getChargingState()
        if not state:
            self.ADapi.log(
                f"{self.charger} state = None when trying to startCharging",
                level = 'WARNING'
            )
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
                        self.ADapi.log(f"Check Charging Handler stopped when Starting to charge. Should only occur when stopping/starting charging in close proximity") ###
                        return False
                self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)
                return True
            else:
                self.ADapi.log(f"{self.charger} was already charging when trying to startCharging") ###
        return False


    def stopCharging(self):
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
                    self.ADapi.log(f"Check Charging Handler stopped when Stopping to charge. Should only occur when stopping/starting charging in close proximity") ###
                    return False
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)
            return True
        return False


    def checkIfChargingStarted(self, kwargs):
        if not self.isAvailable():
            self.wakeMeUp()
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
                self.ADapi.log(f"Check Charging Handler stopped when checking if charging started. Should only occur when stopping/starting charging in close proximity") ###
                return False
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStarted, 60)
            return False
        return True


    def checkIfChargingStopped(self, kwargs):
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
                self.ADapi.log(f"Check Charging Handler stopped when checking if charging stopped. Should only occur when stopping/starting charging in close proximity") ###
                return False
            self.checkCharging_handler = self.ADapi.run_in(self.checkIfChargingStopped, 60)
            return False
        return True


class Tesla(Charger):
    """ Tesla
        Child class of Charger for start/stop/adjust via Tesla custom integration. https://github.com/alandtse/tesla Easiest installation is via HACS.
    
        Selection of possible commands to API
            self.ADapi.call_service('tesla_custom/api', command = 'STOP_CHARGE', parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep': True} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_LIMIT', parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'percent': '70'} )
            self.ADapi.call_service('tesla_custom/api', command = 'CHANGE_CHARGE_MAX', parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}} )  #?
            self.ADapi.call_service('tesla_custom/api', command = 'CHARGING_AMPS', parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'charging_amps': '25'} )

        States returned from charger sensor is:
            if self.get_state(self.charger_sensor, attribute = 'charging_state') != 'Complete': #'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected'
    """

    def __init__(self, api,
        charger = None, # Unique name of charger/car
        charger_sensor = None, # Sensor Plugged in or not with charging states
        charger_switch = None, # Switch Charging or not
        charging_amps = None, # Input Number Amps to charge
        charger_power = None, # Charger power in kW. Contains volts and phases
        charge_limit = None, # SOC limit sensor
        asleep_sensor = None, # If car is sleeping
        online_sensor = None, # If car is online
        battery_sensor = None, # SOC (State Of Charge)
        location_tracker = None, # Location of car/charger
        destination_location_tracker = None, # Destination of car
        arrival_time = None, # Sensor with Arrival time, estimated energy at arrival and destination.
        software_update = None, # If Tesla updates software it can`t change or stop charging
        force_data_update = None, # Button to force car to send update to HA
        polling_switch = None,
        data_last_update_time = None,
        pref_charge_limit = 90, # User input if prefered SOC limit is other than 90%
        charge_on_solar = False,
        battery_size = 100, # User input size of battery. Used to calculate amount of time to charge
        namespace = None,
        finishByHour = None, # HA input_number for when car should be finished charging
        priority = 3, # Priority. See full description
        charge_now = None, # HA input_boolean to bypass smartcharge if true
        electric_consumption = None, # If you have a sensor with measure on watt consumption. Can be one sensor for many chargers
        departure = None # HA input_datetime for when to have car finished charging to 100%. To be written.
    ):

        global JSON_PATH

        self.ADapi = api

        self.charger = charger
        self.charger_sensor = charger_sensor
        self.charger_switch = charger_switch
        self.charging_amps = charging_amps
        self.charger_power = charger_power
        self.charge_limit = charge_limit
        self.asleep_sensor = asleep_sensor
        self.online_sensor = online_sensor
        self.battery_sensor = battery_sensor
        self.location_tracker = location_tracker
        self.destination_location_tracker = destination_location_tracker
        self.arrival_time = arrival_time
        self.software_update = software_update
        self.force_data_update = force_data_update
        self.polling_switch = polling_switch
        self.data_last_update_time = data_last_update_time

        if not self.charger and self.charger_sensor:
            name:str = self.charger_sensor
            name = name.replace(name,'binary_sensor.','')
            name = name.replace(name,'_charger','')
            self.charger = name

        sensor_states = self.ADapi.get_state(entity='sensor')
        for sensor_id, sensor_states in sensor_states.items():
            #self.ADapi.log(f"SensorID: {sensor_id}")
            if 'binary_sensor.' + self.charger + '_charger' in sensor_id:
                if not self.charger_sensor:
                    self.charger_sensor = sensor_id
            if 'switch.' + self.charger + '_charger' in sensor_id:
                if not self.charger_switch:
                    self.charger_switch = sensor_id
            if 'number.' + self.charger + '_charging_amps' in sensor_id:
                if not self.charging_amps:
                    self.charging_amps = sensor_id
            if 'sensor.' + self.charger + '_charger_power' in sensor_id:
                if not self.charger_power:
                    self.charger_power = sensor_id
            if 'number.' + self.charger + '_charge_limit' in sensor_id:
                if not self.charge_limit:
                    self.charge_limit = sensor_id
            if 'binary_sensor.' + self.charger + '_asleep' in sensor_id:
                if not self.asleep_sensor:
                    self.asleep_sensor = sensor_id
            if 'binary_sensor.' + self.charger + '_online' in sensor_id:
                if not self.online_sensor:
                    self.online_sensor = sensor_id
            if 'sensor.' + self.charger + '_battery' in sensor_id:
                if not self.battery_sensor:
                    self.battery_sensor = sensor_id
            if 'device_tracker.' + self.charger + '_location_tracker' in sensor_id:
                if not self.location_tracker:
                    self.location_tracker = sensor_id
            if 'device_tracker.' + self.charger + '_destination_location_tracker' in sensor_id:
                if not self.destination_location_tracker:
                    self.destination_location_tracker = sensor_id
            if 'sensor.' + self.charger + '_arrival_time' in sensor_id:
                if not self.arrival_time:
                    self.arrival_time = sensor_id
            if 'update.' + self.charger + '_software_update' in sensor_id:
                if not self.software_update:
                    self.software_update = sensor_id
            if 'button.' + self.charger + '_force_data_update' in sensor_id:
                if not self.force_data_update:
                    self.force_data_update = sensor_id
            if 'switch.' + self.charger + '_polling' in sensor_id:
                if not self.polling_switch:
                    self.polling_switch = sensor_id
            if 'sensor.' + self.charger + '_data_last_update_time' in sensor_id:
                if not self.data_last_update_time:
                    self.data_last_update_time = sensor_id

        if not self.charger_sensor:
            raise Exception (
                f"charger_sensor not defined or found. Please provide 'charger_sensor' in args for {self.charger}"
            )
        if not self.charger_switch:
            raise Exception (
                f"charger_switch not defined or found. Please provide 'charger_switch' in args for {self.charger}"
            )
        if not self.charging_amps:
            raise Exception (
                f"charging_amps not defined or found. Please provide 'charging_amps' in args for {self.charger}"
            )
        if not self.charger_power:
            raise Exception (
                f"charger_power not defined or found. Please provide 'charger_power' in args for {self.charger}"
            )
        if not self.charge_limit:
            raise Exception (
                f"charge_limit not defined or found. Please provide 'charge_limit' in args for {self.charger}"
            )
        if not self.asleep_sensor:
            raise Exception (
                f"asleep_sensor not defined or found. Please provide 'asleep_sensor' in args for {self.charger}"
            )
        if not self.online_sensor:
            raise Exception (
                f"online_sensor not defined or found. Please provide 'online_sensor' in args for {self.charger}"
            )
        if not self.battery_sensor:
            raise Exception (
                f"battery_sensor not defined or found. Please provide 'battery_sensor' in args for {self.charger}"
            )
        if not self.location_tracker:
            raise Exception (
                f"location_tracker not defined or found. Please provide 'location_tracker' in args for {self.charger}"
            )
        if not self.destination_location_tracker:
            raise Exception (
                f"destination_location_tracker not defined or found. Please provide 'destination_location_tracker' in args for {self.charger}"
            )
        if not self.arrival_time:
            raise Exception (
                f"arrival_time not defined or found. Please provide 'arrival_time' in args for {self.charger}"
            )
        if not self.software_update:
            raise Exception (
                f"software_update not defined or found. Please provide 'software_update' in args for {self.charger}"
            )
        if not self.force_data_update:
            raise Exception (
                f"force_data_update not defined or found. Please provide 'force_data_update' in args for {self.charger}"
            )
        if not self.polling_switch:
            raise Exception (
                f"polling_switch not defined or found. Please provide 'polling_switch' in args for {self.charger}"
            )
        if not self.data_last_update_time:
            raise Exception (
                f"force_data_update not defined or found. Please provide 'force_data_update' in args for {self.charger}"
            )


        super().__init__(
            battery_size = battery_size,
            namespace = namespace,
            finishByHour = finishByHour, # HA input_number for when car should be finished charging
            priority = priority, # Priority. See full description
            charge_now = charge_now, # HA input_boolean to bypass smartcharge if true
            pref_charge_limit = pref_charge_limit, # User input if prefered SOC limit is other than 90%
            charge_on_solar = charge_on_solar,
            electric_consumption = electric_consumption, # If you have a sensor with measure on watt consumption. Can be one sensor for many chargers
            departure = departure # HA input_datetime for when to have car finished charging to 100%. To be written.
        )

        self.vehicle_id = self.ADapi.get_state(self.online_sensor,
            attribute = 'id'
        )

        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.vehicle_id in ElectricityData['charger']:
            ElectricityData['charger'].update(
                {self.vehicle_id : {"voltPhase" : self.voltphase}}
            )
            if self.ADapi.get_state(self.charging_amps) != 'unavailable':
                ElectricityData['charger'][self.vehicle_id].update(
                    {"MaxAmp" :  math.ceil(float(self.ADapi.get_state(self.charging_amps, attribute = 'max')))}
                )
            else:
                ElectricityData['charger'][self.vehicle_id].update(
                    {"MaxAmp" :  6 }
                )
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)

        if (
            self.voltphase == 220
            and self.ADapi.get_state(self.location_tracker) == 'home'
        ):
            self.voltphase = int(ElectricityData['charger'][self.vehicle_id]['voltPhase'])
        self.car_limit_max_charging = math.ceil(float(ElectricityData['charger'][self.vehicle_id]['MaxAmp']))

        self.kWhRemainToCharge = -1
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit)

        self.ADapi.listen_state(self.ChargingStarted, self.charger_switch, new = 'on')
        self.ADapi.listen_state(self.ChargingStopped, self.charger_switch, new = 'off')
        self.ADapi.listen_state(self.ChargingConnected, self.charger_sensor)
        self.ADapi.listen_state(self.ChargeLimitChanged, self.charge_limit)
        """ TODO:
            Add Maxrange solution for charging finished to 100% at given time.
            #self.ADapi.listen_state(self.MaxRangeListener, self.departure, duration = 5 )
        """

        self.ADapi.run_in(self.whenStartedUp, 80)


    def setVoltPhase(self):
        global JSON_PATH
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        ChargerInfo = ElectricityData['charger'][self.vehicle_id]
        try:
            volts = int(self.ADapi.get_state(self.charger_power,
                attribute = 'charger_volts')
            )
            phases = int(self.ADapi.get_state(self.charger_power,
                attribute = 'charger_phases')
            )
            if (
                phases == 3
                and volts > 200
                and volts < 250
            ):
                self.voltphase = 266
                if self.getLocation() == 'home':
                    ChargerInfo.update(
                        { "voltPhase" : self.voltphase}
                    )
                    ElectricityData['charger'][self.vehicle_id].update(ChargerInfo)
                self.ADapi.log(f"VoltPhase set to 266 for {self.charger}", level = 'DEBUG')

            elif (
                phases == 3
                and volts > 300
            ):
                self.voltphase = 687
                if self.getLocation() == 'home':
                    ChargerInfo.update(
                        { "voltPhase" : self.voltphase}
                    )
                    ElectricityData['charger'][self.vehicle_id].update(ChargerInfo)
                self.ADapi.log(f"VoltPhase set to 400v for {self.charger}", level = 'DEBUG')

            elif (
                phases == 1
                and volts > 200
                and volts < 250
            ):
                self.voltphase = volts
                if self.getLocation() == 'home':
                    ChargerInfo.update(
                        { "voltPhase" : self.voltphase}
                    )
                    ElectricityData['charger'][self.vehicle_id].update(ChargerInfo)
                self.ADapi.log(f"VoltPhase set to {volts} for {self.charger}", level = 'DEBUG')

            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)

        except TypeError as te:
            self.ADapi.log(
                f"VoltPhase TypeError for {self.charger}. TypeError {te}",
                level = 'DEBUG'
            )
        except Exception as e:
            self.ADapi.log(
                f"VoltPhase could not be set for {self.charger}. Exception: {e}",
                level = 'DEBUG'
            )


    def SoftwareUpdates(self):
        if (
            self.ADapi.get_state(self.software_update) != 'unknown'
            and self.ADapi.get_state(self.software_update) != 'unavailable'
        ):
            if self.ADapi.get_state(self.software_update, attribute = 'in_progress') != False:
                return True
        return False


    def dontStopMeNow(self):
        if super().dontStopMeNow():
            return True
        if (
            self.ADapi.get_state(self.charge_limit) != 'unknown'
            and self.ADapi.get_state(self.charge_limit) != 'unavailable'
        ):
            return int(self.ADapi.get_state(self.charge_limit)) > 90
        return False


    def wakeMeUp(self):
        if self.ADapi.get_state(self.polling_switch) == 'on':
            if (
                self.getChargingState() != 'Complete'
                and self.getChargingState() != 'Disconnected'
            ):
                if not self.recentlyUpdated():
                    self.ADapi.call_service('tesla_custom/api',
                        command = 'WAKE_UP',
                        parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep' : True}
                    )
                    self.ADapi.log(f"Waking up {self.charger}") ###


    def recentlyUpdated(self):
        last_update = self.ADapi.convert_utc(self.ADapi.get_state(self.data_last_update_time))
        now: datetime = self.ADapi.datetime(aware=True)
        stale_time: timedelta = now - last_update
        if stale_time > datetime.timedelta(minutes = 12):
            return True
        return False


    def forceDataUpdate(self):
        self.ADapi.call_service('button/press',
            entity_id = self.force_data_update
        )

    def isAvailable(self):
        charging_state:str = self.getChargingState()
        if not charging_state :
            self.wakeMeUp()
        elif charging_state != 'NoPower':
            if super().isAvailable():
                if (
                    self.ADapi.get_state(self.charger_sensor) == 'on'
                    and self.ADapi.get_state(self.charger_switch) != 'unknown'
                    and self.ADapi.get_state(self.charger_switch) != 'unavailable'
                    and self.ADapi.get_state(self.battery_sensor) != 'unknown'
                    and self.ADapi.get_state(self.battery_sensor) != 'unavailable'
                    and self.ADapi.get_state(self.charger_power) != 'unknown'
                    and self.ADapi.get_state(self.charger_power) != 'unavailable'
                ):
                    return True
        return False


    def getLocation(self):
        return self.ADapi.get_state(self.location_tracker)


        #'Complete' / 'None' / 'Stopped' / 'Charging' / 'Disconnected' / 'Starting'
        # TODO: Return someting valid if unavailable
    def getChargingState(self):
        try:
            state = self.ADapi.get_state(self.charger_sensor, attribute = 'charging_state')
            if state == 'Starting':
                state = 'Charging'
            return state
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Could not getChargingState: {self.ADapi.get_state(self.charger_sensor)} ValueError: {ve}",
                level = 'WARNING'
            ) ### DEBUG
            return None
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Could not getChargingState: {self.ADapi.get_state(self.charger_sensor)} TypeError: {te}",
                level = 'WARNING'
            ) ### DEBUG
            return None
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not getChargingState: {self.ADapi.get_state(self.charger_sensor)} Exception: {e}",
                level = 'WARNING'
            )
            return None

    def getChargerPower(self):
        try:
            return float(self.ADapi.get_state(self.charger_power)) *1000
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {self.ADapi.get_state(self.charger_power)} ValueError: {ve}",
                level = 'DEBUG'
            )
            return 0
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {self.ADapi.get_state(self.charger_power)} TypeError: {te}",
                level = 'WARNING'
            )
            return 0
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not get charger_power: {self.ADapi.get_state(self.charger_power)} Exception: {e}",
                level = 'WARNING'
            )
            return 0


    def maxChargingAmps(self):
        try:
            max_charging_amps = math.ceil(float(self.ADapi.get_state(self.charging_amps, attribute = 'max')))
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Could not get maxChargingAmps. ValueError: {ve}",
                level = 'WARNING'
            ) ### DEBUG
            max_charging_amps = 32
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Could not get maxChargingAmps. TypeError: {te}",
                level = 'WARNING'
            ) ### DEBUG
            max_charging_amps =  32
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Could not get maxChargingAmps. Exception: {e}",
                level = 'WARNING'
            )
            max_charging_amps =  32

        if max_charging_amps > self.car_limit_max_charging:
            self.car_limit_max_charging = max_charging_amps
            with open(JSON_PATH, 'r') as json_read:
                ElectricityData = json.load(json_read)

            ChargerInfo = ElectricityData['charger'][self.vehicle_id]
            ChargerInfo.update(
                { "MaxAmp" : self.car_limit_max_charging}
            )
            ElectricityData['charger'][self.vehicle_id].update(ChargerInfo)
            with open(JSON_PATH, 'w') as json_write:
                json.dump(ElectricityData, json_write, indent = 4)
            self.ADapi.log(f"Max amp set to {self.car_limit_max_charging} for {self.charger}", level = 'INFO') ###
        return self.car_limit_max_charging


    def isChargingAtMaxAmps(self):
        if super().isChargingAtMaxAmps():
            if (
                math.ceil(float(self.ADapi.get_state(self.charging_amps))) == self.ampereCharging
                or math.floor(float(self.ADapi.get_state(self.charging_amps))) == self.ampereCharging
            ):
                return True
        return False


    def kWhRemaining(self):
        try:
            if float(self.ADapi.get_state(self.battery_sensor)) < float(self.ADapi.get_state(self.charge_limit)):
                percentRemainToCharge = float(self.ADapi.get_state(self.charge_limit)) - float(self.ADapi.get_state(self.battery_sensor))
                self.kWhRemainToCharge = (percentRemainToCharge / 100) * self.battery_size
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Not able to calculate kWhRemainToCharge. Return existing value: {self.kWhRemainToCharge}. ValueError: {ve}",
                level = 'WARNING'
            ) ### DEBUG
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Not able to calculate kWhRemainToCharge. Return existing value: {self.kWhRemainToCharge}. TypeError: {te}",
                level = 'WARNING'
            ) ### DEBUG
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Not able to calculate kWhRemainToCharge. Exception: {e}",
                level = 'WARNING'
            )
        return self.kWhRemainToCharge


    def state_of_charge(self):
        try:
            SOC = float(self.ADapi.get_state(self.battery_sensor))
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Not able to get SOC. Return value: {self.pref_charge_limit}. ValueError: {ve}",
                level = 'WARNING'
            ) ### DEBUG
            SOC = self.pref_charge_limit
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Not able to get SOC. Return value: {self.pref_charge_limit}. TypeError: {te}",
                level = 'WARNING'
            ) ### DEBUG
            SOC = self.pref_charge_limit
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Not able to calculate kWhRemainToCharge. Exception: {e}",
                level = 'WARNING'
            )
            SOC = self.pref_charge_limit
        return SOC


    def setChargingAmps(self, charging_amp_set = 16):
        charging_amp_set = super().setChargingAmps(charging_amp_set = charging_amp_set)
        self.ADapi.call_service('tesla_custom/api',
            command = 'CHARGING_AMPS',
            parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'charging_amps': charging_amp_set}
        )


    def changeChargeLimit(self, chargeLimit = 90 ):
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit)
        self.ADapi.call_service('tesla_custom/api',
            command = 'CHANGE_CHARGE_LIMIT',
            parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'percent': chargeLimit}
        )


        # Listen states
    def ChargingConnected(self, entity, attribute, old, new, kwargs):
        global CHARGE_SCHEDULER
        self.setVoltPhase()

        if self.getLocation() == 'home':
            if (
                new == 'on'
                and self.kWhRemaining() > 0
            ):
                if self.ADapi.get_state(self.charger_switch) == 'on':
                    return # Calculations will be handeled by ChargingStarted

                self.findNewChargeTime()

            elif new == 'off':
                if self.hasChargingScheduled():
                    CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)
                if self.max_range_handler != None:
                    # TODO: Program charging to max at departure time.
                    # @HERE: Call a function that will cancel handler when car is disconnected
                    #self.ADapi.run_in(self.resetMaxRangeCharging, 1)
                    self.ADapi.log(f"{self.charger} Has a max_range_handler. Not Programmed yet", level = 'DEBUG') ###


    def ChargeLimitChanged(self, entity, attribute, old, new, kwargs):
        try:
            self.oldChargeLimit = new
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} new charge limit: {new}. Error: {ve}",
                level = 'DEBUG'
            )
        except Exception as e:
            self.ADapi.log(
                f"Not able to process {self.charger} new charge limit: {new}. Exception: {e}",
                level = 'WARNING'
            )
        if self.getLocation() == 'home':
            if float(self.ADapi.get_state(self.battery_sensor)) > float(new):
                if self.hasChargingScheduled():
                    CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)
                    self.kWhRemainToCharge = -1

            elif int(new) <= 90:
                if not self.findNewChargeTime():
                    self.stopCharging()

            elif int(new) > 90:
                self.startCharging()



    def ChargingStarted(self, entity, attribute, old, new, kwargs):
        global CHARGE_SCHEDULER
        if self.getLocation() == 'home':
            if not self.hasChargingScheduled():
                if not self.findNewChargeTime():
                    self.stopCharging()

            elif not CHARGE_SCHEDULER.isChargingTime():
                self.ADapi.log(f"{self.charger} ChargingStarted. isChargingTime stopper lading", level = 'INFO') ###
                self.stopCharging()


    def ChargingStopped(self, entity, attribute, old, new, kwargs):
        global CHARGE_SCHEDULER
        global RECIPIENTS
        try:
            if (
                self.kWhRemaining() <= 2
                or CHARGE_SCHEDULER.isPastChargingTime()
            ):
                if self.getChargingState() == 'Complete':
                    CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)
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

                    self.setChargingAmps(charging_amp_set = 6) # Set to 6 amp for preheat... CHECKME

            self.ampereCharging = 0

        except AttributeError as ae:
            self.ADapi.log(f"Attribute Error in ChargingStopped: {ae}", level = 'DEBUG')
        except Exception as e:
            self.ADapi.log(f"Exception in ChargingStopped: {e}", level = 'WARNING')


    def startCharging(self):
        if super().startCharging():
            try:
                self.ADapi.call_service('tesla_custom/api',
                    command = 'START_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep': True}
                )
                #self.forceDataUpdate()
                #self.ADapi.call_service('switch/turn_on', entity_id = self.charger_switch)
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception: {e}", level = 'WARNING')

        elif self.getChargingState() == 'Complete':
             CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)
        else:
            self.ADapi.log(f"Not ready to StartCharging {self.charger} from charger class. Check for errors", level = 'WARNING') ### TODO: Find out if any errors causes this


    def stopCharging(self):
        if super().stopCharging():
            try:
                self.ADapi.call_service('tesla_custom/api',
                    command = 'STOP_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep': True}
                )
                #self.forceDataUpdate()
                #self.ADapi.call_service('switch/turn_off', entity_id = self.charger_switch)
                self.ADapi.log(f"StopCharging {self.charger} from charger class", level = 'INFO') ###
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging: {e}", level = 'WARNING')


    def checkIfChargingStarted(self, kwargs):
        if not super().checkIfChargingStarted(0):
            self.forceDataUpdate()
            try:
                self.ADapi.call_service('tesla_custom/api',
                    command = 'START_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep': True}
                )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Start Charging in checkIfChargingStarted for {self.charger}. Exception: {e}",
                    level = 'DEBUG'
                )


    def checkIfChargingStopped(self, kwargs):
        if not super().checkIfChargingStopped(0):
            try:
                self.ADapi.call_service('tesla_custom/api',
                    command = 'STOP_CHARGE',
                    parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'wake_if_asleep': True}
                )
            except Exception as e:
                self.ADapi.log(
                    f"Could not Stop Charging in checkIfChargingStopped for {self.charger}. Exception: {e}",
                    level = 'DEBUG'
                )


""" Easee
    Child class of Charger

    @Pythm / https://github.com/Pythm
"""

class Easee(Charger):


    def __init__(self, api,
        charger = None, # Unique name of charger/car
        charger_status = None, # Status
        reason_for_no_current = None, # Switch Charging or not
        current = None, # Input Number Amps to charge
        charger_power = None, # Charger power in kW.
        voltage = None, # SOC limit sensor
        max_charger_limit = None, # 
        online_sensor = None, # If car is online
        session_energy = None,
        battery_size = 15, # User input size of battery. Used to calculate amount of time to charge
        namespace = None,
        finishByHour = None, # HA input_number for when car should be finished charging
        priority = 3, # Priority. See full description
        charge_now = None, # HA input_boolean to bypass smartcharge if true
        pref_charge_limit = 100,
        charge_on_solar = False,
        electric_consumption = None, # If you have a sensor with measure on watt consumption. Can be one sensor for many chargers
        departure = None, # HA input_datetime for when to have car finished charging to 100%. To be written.
        guest = None
    ):

        global JSON_PATH

        self.ADapi = api

        self.charger = charger
        self.charger_status = charger_status
        self.reason_for_no_current = reason_for_no_current
        self.charging_amps = current
        self.charger_power = charger_power
        self.voltage = voltage
        self.max_charger_limit = max_charger_limit
        self.online_sensor = online_sensor
        self.session_energy = session_energy
        if not guest:
            self.guestCharging = False
        else:
            self.guestCharging = self.ADapi.get_state(guest) == 'on'
            self.ADapi.listen_state(self.guestChargingListen, guest)

        if not self.charger and self.charger_status:
            name:str = self.charger_status
            name = name.replace(name,'sensor.','')
            name = name.replace(name,'_status','')
            self.charger = name

        sensor_states = self.ADapi.get_state(entity='sensor')
        for sensor_id, sensor_states in sensor_states.items():
            if 'sensor.' + self.charger + '_status' in sensor_id:
                if not self.charger_status:
                    self.charger_status = sensor_id
            if 'sensor.' + self.charger + '_reason_for_no_current' in sensor_id:
                if not self.reason_for_no_current:
                    self.reason_for_no_current = sensor_id
            if 'sensor.' + self.charger + '_current' in sensor_id:
                if not self.charging_amps:
                    self.charging_amps = sensor_id
            if 'sensor.' + self.charger + '_power' in sensor_id:
                if not self.charger_power:
                    self.charger_power = sensor_id
            if 'sensor.' + self.charger + '_voltage' in sensor_id:
                if not self.voltage:
                    self.voltage = sensor_id
            if 'sensor.' + self.charger + '_max_charger_limit' in sensor_id:
                if not self.max_charger_limit:
                    self.max_charger_limit = sensor_id
            if 'binary_sensor.' + self.charger + '_online' in sensor_id:
                if not self.online_sensor:
                    self.online_sensor = sensor_id
            if 'sensor.' + self.charger + '_session_energy' in sensor_id:
                if not self.session_energy:
                    self.session_energy = sensor_id

        if not self.charger_status:
            raise Exception (
                f"charger_status not defined or found. Please provide 'charger_status' in args for {self.charger}"
            )
        if not self.reason_for_no_current:
            raise Exception (
                f"reason_for_no_current not defined or found. Please enable 'reason_for_no_current' sensor in Easee integration for {self.charger}"
            )
        if not self.charging_amps:
            raise Exception (
                f"current not defined or found. Please enable 'current' sensor in Easee integration for {self.charger}"
            )
        if not self.charger_power:
            raise Exception (
                f"charger_power not defined or found. Please enable 'charger_power' sensor in Easee integration for {self.charger}"
            )
        if not self.voltage:
            raise Exception (
                f"voltage not defined or found. Please enable 'voltage' sensor in Easee integration for {self.charger}"
            )
        if not self.max_charger_limit:
            raise Exception (
                f"max_charger_limit not defined or found. Please enable 'max_charger_limit' sensor in Easee integration for {self.charger}"
            )
        if not self.online_sensor:
            raise Exception (
                f"online_sensor not defined or found. Please provide 'online_sensor' in args for {self.charger}"
            )
        if not self.session_energy:
            raise Exception (
                f"session_energy not defined or found. Please enable 'session_energy' sensor in Easee integration for {self.charger}"
            )

        super().__init__(
            battery_size = battery_size,
            namespace = namespace,
            finishByHour = finishByHour, # HA input_number for when car should be finished charging
            priority = priority, # Priority. See full description
            charge_now = charge_now, # HA input_boolean to bypass smartcharge if true
            pref_charge_limit = pref_charge_limit,
            charge_on_solar = charge_on_solar,
            electric_consumption = electric_consumption, # If you have a sensor with measure on watt consumption. Can be one sensor for many chargers
            departure = departure # HA input_datetime for when to have car finished charging to 100%. To be written.
        )

        self.vehicle_id = self.ADapi.get_state(self.charger_status,
            attribute = 'id'
        )

        volts = self.ADapi.get_state(self.voltage)
        try:
            volts = math.ceil(float(volts))
            phases = int(self.ADapi.get_state(self.charger_status,
                attribute = 'config_phaseMode')
            )
            if (
                phases == 3
                and volts > 200
                and volts < 250
            ):
                self.voltphase = 266
            elif (
                phases == 3
                and volts > 300
            ):
                self.voltphase = 687
            elif (
                phases == 1
                and volts > 200
                and volts < 250
            ):
                self.voltphase = volts

        except ValueError:
            self.voltphase = 230
        except Exception as e:
            self.ADapi.log(f"Error trying to get voltage: {volts}. Exception: {e}", level = 'WARNING')

            # Find max kWh charged from charger during one session.
        with open(JSON_PATH, 'r') as json_read:
            ElectricityData = json.load(json_read)
        if not self.vehicle_id in ElectricityData['charger']:
            if self.ADapi.get_state(self.max_charger_limit) != 'unavailable':
                ElectricityData['charger'].update(
                    {self.vehicle_id : {"MaxkWhCharged" : 1, "MaxAmp" : math.ceil(float(self.ADapi.get_state(self.max_charger_limit)))}}
                )
        ChargerInfo = ElectricityData['charger'][self.vehicle_id]

        self.maxkWhCharged = float(ElectricityData['charger'][self.vehicle_id]['MaxkWhCharged'])
        self.car_limit_max_charging = math.ceil(float(ElectricityData['charger'][self.vehicle_id]['MaxAmp']))

        if self.session_energy:
            session = float(self.ADapi.get_state(self.session_energy))/1000
            if self.maxkWhCharged < session:
                self.maxkWhCharged = session
                ChargerInfo.update(
                    { "MaxkWhCharged" : self.maxkWhCharged}
                )
                ElectricityData['charger'][self.vehicle_id].update(ChargerInfo)
                with open(JSON_PATH, 'w') as json_write:
                    json.dump(ElectricityData, json_write, indent = 4)

        self.ADapi.run_in(self.whenStartedUp, 81)
        self.ADapi.listen_state(self.statusChange, self.charger_status)
        self.ADapi.listen_state(self.reasonChange, self.reason_for_no_current)


    def isAvailable(self):
        if super().isAvailable():
            charging_state:str = self.getChargingState()
            if (
                charging_state != 'Complete'
                and charging_state != 'Disconnected'
                and charging_state != 'NoPower'
            ):
                return True
        return False


        #'awaiting_start' / 'charging' / 'completed' / 'disconnected' / from charger_status
        # Return: Charging / Complete / 'Disconnected' / 'NoPower' / 'Stopped' / 'Starting'
    def getChargingState(self):
        status = self.ADapi.get_state(self.charger_status)
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


    def maxChargingAmps(self):
        if self.guestCharging:
            self.ADapi.log(f"Max charge on guest: {math.ceil(float(self.ADapi.get_state(self.max_charger_limit)))}") ###
            return math.ceil(float(self.ADapi.get_state(self.max_charger_limit)))
        return self.car_limit_max_charging


    def kWhRemaining(self):
        status = self.ADapi.get_state(self.charger_status)
        if (
            status == 'completed'
            or status == 'disconnected'
        ):
            return 0

        elif self.session_energy:
            if self.guestCharging:
                return 100 - (float(self.ADapi.get_state(self.session_energy))/1000)
            return self.maxkWhCharged - (float(self.ADapi.get_state(self.session_energy))/1000) +1


    def setChargingAmps(self, charging_amp_set = 16):
        charging_amp_set = super().setChargingAmps(charging_amp_set = charging_amp_set)
        self.ADapi.call_service('easee/set_charger_dynamic_limit',
            current = charging_amp_set,
            charger_id = self.vehicle_id
        )


        # Listen states
        #'awaiting_start' / 'charging' / 'completed' / 'disconnected' / 'ready_to_charge' / from charger_status
    def statusChange(self, entity, attribute, old, new, kwargs):
        global CHARGE_SCHEDULER
        global JSON_PATH

        if (
            new == 'awaiting_start'
            and old == 'disconnected'
        ):
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
            CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)
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

            if self.session_energy:
                if self.guestCharging:
                    return

                session = float(self.ADapi.get_state(self.session_energy))/1000
                if self.maxkWhCharged < session:
                    self.maxkWhCharged = session
                        # Find max kWh charged from charger during one session.
                    with open(JSON_PATH, 'r') as json_read:
                        ElectricityData = json.load(json_read)

                    ChargerInfo = ElectricityData['charger'][self.vehicle_id]
                    ChargerInfo.update(
                        { "MaxkWhCharged" : self.maxkWhCharged}
                    )
                    ElectricityData['charger'][self.vehicle_id].update(ChargerInfo)
                    with open(JSON_PATH, 'w') as json_write:
                        json.dump(ElectricityData, json_write, indent = 4)
                    self.ADapi.log(f"{self.charger} maxkWhCharged updated to = {self.maxkWhCharged }", level = 'INFO') ###

        elif new == 'disconnected':
            CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)
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

            self.car_limit_max_charging = int(self.ADapi.get_state(self.max_charger_limit))


        #'no_current_request' / 'undefined' / 'waiting_in_queue' / 'limited_by_charger_max_limit' / 'limited_by_local_adjustment' / 'limited_by_car' from reason_for_no_current
        # 'car_not_charging' / 
    def reasonChange(self, entity, attribute, old, new, kwargs):
        global JSON_PATH

        if new == 'limited_by_car':
            if self.guestCharging:
                return

            max_charging_amps = math.ceil(float(self.ADapi.get_state(self.charging_amps)))
            if self.car_limit_max_charging != max_charging_amps:
                self.car_limit_max_charging = max_charging_amps
                with open(JSON_PATH, 'r') as json_read:
                    ElectricityData = json.load(json_read)

                ChargerInfo = ElectricityData['charger'][self.vehicle_id]
                ChargerInfo.update(
                    { "MaxAmp" : self.car_limit_max_charging}
                )
                ElectricityData['charger'][self.vehicle_id].update(ChargerInfo)
                with open(JSON_PATH, 'w') as json_write:
                    json.dump(ElectricityData, json_write, indent = 4)
                self.ADapi.log(f"Max amp set to {self.car_limit_max_charging} for {self.charger}", level = 'INFO') ###


    def startCharging(self):
        if super().startCharging():
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'resume',
                    charger_id = self.vehicle_id
                ) # start
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Start Charging. Exception {e}", level = 'WARNING')

        elif self.getChargingState() == 'Complete':
            CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)


    def stopCharging(self):
        if super().stopCharging():
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'pause',
                    charger_id = self.vehicle_id
                ) # stop
            except Exception as e:
                self.ADapi.log(f"{self.charger} Could not Stop Charging. Exception: {e}", level = 'WARNING')

        elif (
            not self.dontStopMeNow()
            and self.ADapi.get_state(self.charger_status) == 'awaiting_start'
        ):
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'pause',
                    charger_id = self.vehicle_id
                ) # stop
            except Exception as e:
                self.ADapi.log(
                    f"{self.charger} Could not Stop Charging while awaiting start. Exception: {e}",
                    level = 'WARNING'
                )
            self.ADapi.run_in(self.checkIfChargingStopped, 60)


    def checkIfChargingStarted(self, kwargs):
        if not super().checkIfChargingStarted(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'resume',
                    charger_id = self.vehicle_id
                    ) # start
                self.ADapi.log(f"{self.charger} Try Start Charging in checkIfChargingStarted", level = 'INFO') ###
            except Exception as e:
                self.ADapi.log(
                    f"Could not Start Charging in checkIfChargingStarted for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )


    def checkIfChargingStopped(self, kwargs):
        if not super().checkIfChargingStopped(0):
            try:
                self.ADapi.call_service('easee/action_command',
                    action_command = 'pause',
                    charger_id = self.vehicle_id
                    ) # stop
            except Exception as e:
                self.ADapi.log(
                    f"Could not Stop Charging in checkIfChargingStopped for {self.charger}. Exception: {e}",
                    level = 'WARNING'
                )

    def guestChargingListen(self, entity, attribute, old, new, kwargs):
        self.guestCharging = self.ADapi.get_state(entity) == 'on'
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


class Tesla_Easee(Easee):
    """ Tesla charging on a Easee charger
        Child class of Easee for start/stop/adjust.
        Battery state via Tesla custom integration. https://github.com/alandtse/tesla Easiest installation is via HACS.
    
    """

    def __init__(self, api,
        # Charger:
        charger = None, # Unique name of charger
        charger_status = None, # Status
        reason_for_no_current = None, # Switch Charging or not
        current = None, # Input Number Amps to charge
        charger_power = None, # Charger power in kW.
        voltage = None, # SOC limit sensor
        max_charger_limit = None, # 
        online_sensor = None, # If charger is online
        session_energy = None,

        # Car:
        car = None, # Unique name of car
        charger_sensor = None, # Sensor Plugged in or not with charging states
        #charger_switch = None, # Switch Charging or not
        #charging_amps = None, # Input Number Amps to charge
        #charger_power = None, # Charger power in kW. Contains volts and phases
        charge_limit = None, # SOC limit sensor
        #asleep_sensor = None, # If car is sleeping
        #online_sensor = None, # If car is online
        battery_sensor = None, # SOC (State Of Charge)
        location_tracker = None, # Location of car/charger
        destination_location_tracker = None, # Destination of car
        arrival_time = None, # Sensor with Arrival time, estimated energy at arrival and destination.
        software_update = None, # If Tesla updates software it can`t change or stop charging
        force_data_update = None, # Button to force car to send update to HA
        polling_switch = None,
        data_last_update_time = None,

        # HA sensors/ inputs/ preferences
        pref_charge_limit = 90, # User input if prefered SOC limit is other than 90%
        charge_on_solar = False,
        battery_size = 100, # User input size of battery. Used to calculate amount of time to charge
        namespace = None,
        finishByHour = None, # HA input_number for when car should be finished charging
        priority = 3, # Priority. See full description
        charge_now = None, # HA input_boolean to bypass smartcharge if true
        electric_consumption = None, # If you have a sensor with measure on watt consumption. Can be one sensor for many chargers
        departure = None, # HA input_datetime for when to have car finished charging to 100%. To be written.
        guest = None

    ):

        global JSON_PATH

        self.ADapi = api

        """ Charger: Send to parent
        self.charger = charger
        self.charger_status = charger_status
        self.reason_for_no_current = reason_for_no_current
        self.charging_amps = current
        self.charger_power = charger_power
        self.voltage = voltage
        self.max_charger_limit = max_charger_limit
        self.online_sensor = online_sensor
        self.session_energy = session_energy
        if not guest:
            self.guestCharging = False
        else:
            self.guestCharging = self.ADapi.get_state(guest) == 'on'
            self.ADapi.listen_state(self.guestChargingListen, guest)

        if not self.charger and self.charger_status:
            name:str = self.charger_status
            name = name.replace(name,'sensor.','')
            name = name.replace(name,'_status','')
            self.charger = name

        sensor_states = self.ADapi.get_state(entity='sensor')
        for sensor_id, sensor_states in sensor_states.items():
            if 'sensor.' + self.charger + '_status' in sensor_id:
                if not self.charger_status:
                    self.charger_status = sensor_id
            if 'sensor.' + self.charger + '_reason_for_no_current' in sensor_id:
                if not self.reason_for_no_current:
                    self.reason_for_no_current = sensor_id
            if 'sensor.' + self.charger + '_current' in sensor_id:
                if not self.charging_amps:
                    self.charging_amps = sensor_id
            if 'sensor.' + self.charger + '_power' in sensor_id:
                if not self.charger_power:
                    self.charger_power = sensor_id
            if 'sensor.' + self.charger + '_voltage' in sensor_id:
                if not self.voltage:
                    self.voltage = sensor_id
            if 'sensor.' + self.charger + '_max_charger_limit' in sensor_id:
                if not self.max_charger_limit:
                    self.max_charger_limit = sensor_id
            if 'binary_sensor.' + self.charger + '_online' in sensor_id:
                if not self.online_sensor:
                    self.online_sensor = sensor_id
            if 'sensor.' + self.charger + '_session_energy' in sensor_id:
                if not self.session_energy:
                    self.session_energy = sensor_id
        """
        # Car:

        self.car = car # Changed from charger in Tesla class
        self.charger_sensor = charger_sensor
        #self.charger_switch = charger_switch
        #self.charging_amps = charging_amps
        #self.charger_power = charger_power
        self.charge_limit = charge_limit
        #self.asleep_sensor = asleep_sensor
        #self.online_sensor = online_sensor
        self.battery_sensor = battery_sensor
        self.location_tracker = location_tracker
        self.destination_location_tracker = destination_location_tracker
        self.arrival_time = arrival_time
        self.software_update = software_update
        self.force_data_update = force_data_update
        
        self.polling_switch = polling_switch
        self.data_last_update_time = data_last_update_time

        if not self.charger and self.charger_sensor:
            name:str = self.charger_sensor
            name = name.replace(name,'binary_sensor.','')
            name = name.replace(name,'_charger','')
            self.charger = name

        sensor_states = self.ADapi.get_state(entity='sensor')
        for sensor_id, sensor_states in sensor_states.items():
            #self.ADapi.log(f"SensorID: {sensor_id}")
            if 'binary_sensor.' + self.charger + '_charger' in sensor_id:
                if not self.charger_sensor:
                    self.charger_sensor = sensor_id
            if 'number.' + self.charger + '_charge_limit' in sensor_id:
                if not self.charge_limit:
                    self.charge_limit = sensor_id
            if 'sensor.' + self.charger + '_battery' in sensor_id:
                if not self.battery_sensor:
                    self.battery_sensor = sensor_id
            if 'device_tracker.' + self.charger + '_location_tracker' in sensor_id:
                if not self.location_tracker:
                    self.location_tracker = sensor_id
            if 'device_tracker.' + self.charger + '_destination_location_tracker' in sensor_id:
                if not self.destination_location_tracker:
                    self.destination_location_tracker = sensor_id
            if 'sensor.' + self.charger + '_arrival_time' in sensor_id:
                if not self.arrival_time:
                    self.arrival_time = sensor_id
            if 'update.' + self.charger + '_software_update' in sensor_id:
                if not self.software_update:
                    self.software_update = sensor_id
            if 'button.' + self.charger + '_force_data_update' in sensor_id:
                if not self.force_data_update:
                    self.force_data_update = sensor_id
            if 'switch.' + self.charger + '_polling' in sensor_id:
                if not self.polling_switch:
                    self.polling_switch = sensor_id
            if 'sensor.' + self.charger + '_data_last_update_time' in sensor_id:
                if not self.data_last_update_time:
                    self.data_last_update_time = sensor_id

        if not self.charger_sensor:
            raise Exception (
                f"charger_sensor not defined or found. Please provide 'charger_sensor' in args for {self.charger}"
            )
        if not self.charge_limit:
            raise Exception (
                f"charge_limit not defined or found. Please provide 'charge_limit' in args for {self.charger}"
            )
        if not self.battery_sensor:
            raise Exception (
                f"battery_sensor not defined or found. Please provide 'battery_sensor' in args for {self.charger}"
            )
        if not self.location_tracker:
            raise Exception (
                f"location_tracker not defined or found. Please provide 'location_tracker' in args for {self.charger}"
            )
        if not self.destination_location_tracker:
            raise Exception (
                f"destination_location_tracker not defined or found. Please provide 'destination_location_tracker' in args for {self.charger}"
            )
        if not self.arrival_time:
            raise Exception (
                f"arrival_time not defined or found. Please provide 'arrival_time' in args for {self.charger}"
            )
        if not self.software_update:
            raise Exception (
                f"software_update not defined or found. Please provide 'software_update' in args for {self.charger}"
            )
        if not self.force_data_update:
            raise Exception (
                f"force_data_update not defined or found. Please provide 'force_data_update' in args for {self.charger}"
            )
        if not self.polling_switch:
            raise Exception (
                f"polling_switch not defined or found. Please provide 'polling_switch' in args for {self.charger}"
            )
        if not self.data_last_update_time:
            raise Exception (
                f"force_data_update not defined or found. Please provide 'force_data_update' in args for {self.charger}"
            )


        self.kWhRemainToCharge = -1
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit)

        super().__init__(self,
            charger = charger,
            charger_status = charger_status,
            reason_for_no_current = reason_for_no_current,
            current = current,
            charger_power = charger_power,
            voltage = voltage,
            max_charger_limit = max_charger_limit,
            online_sensor = online_sensor,
            session_energy = session_energy,
            battery_size = battery_size,
            namespace = namespace,
            finishByHour = finishByHour,
            priority = priority,
            charge_now = charge_now,
            pref_charge_limit = pref_charge_limit,
            charge_on_solar = charge_on_solar,
            electric_consumption = electric_consumption,
            departure = departure,
            guest = guest
        )

        """ TODO:
            Add Maxrange solution for charging finished to 100% at given time.
            #self.ADapi.listen_state(self.MaxRangeListener, self.departure, duration = 5 )
        """
        self.ADapi.listen_state(self.ChargeLimitChanged, self.charge_limit)


    def SoftwareUpdates(self):
        if (
            self.ADapi.get_state(self.software_update) != 'unknown'
            and self.ADapi.get_state(self.software_update) != 'unavailable'
        ):
            if self.ADapi.get_state(self.software_update, attribute = 'in_progress') != False:
                self.setChargingAmps(charging_amp_set = 6)


    def startCharging(self):
        if (
            (self.ADapi.get_state(self.location_tracker) == 'home'
            and self.ADapi.get_state(self.charger_sensor) == 'on')
            or self.guestCharging
        ):
            super().startCharging()
        else : ### TESTING ONLY
            self.ADapi.log(
                "Not starting to charge. Not home or not connected. "
                f"Location: {self.ADapi.get_state(self.location_tracker)}. "
                f"Connected? {self.ADapi.get_state(self.charger_sensor)}"
            )
    
    def kWhRemaining(self):
        status = self.ADapi.get_state(self.charger_status)
        if (
            status == 'completed'
            or status == 'disconnected'
        ):
            return 0

        try:
            if float(self.ADapi.get_state(self.battery_sensor)) < float(self.ADapi.get_state(self.charge_limit)):
                percentRemainToCharge = float(self.ADapi.get_state(self.charge_limit)) - float(self.ADapi.get_state(self.battery_sensor))
                self.kWhRemainToCharge = (percentRemainToCharge / 100) * self.battery_size
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Not able to calculate kWhRemainToCharge. Return existing value: {self.kWhRemainToCharge}. ValueError: {ve}",
                level = 'WARNING'
            ) ### DEBUG
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Not able to calculate kWhRemainToCharge. Return existing value: {self.kWhRemainToCharge}. TypeError: {te}",
                level = 'WARNING'
            ) ### DEBUG
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Not able to calculate kWhRemainToCharge. Exception: {e}",
                level = 'WARNING'
            )
        return self.kWhRemainToCharge


    def state_of_charge(self):
        try:
            SOC = float(self.ADapi.get_state(self.battery_sensor))
        except ValueError as ve:
            self.ADapi.log(
                f"{self.charger} Not able to get SOC. Return value: {self.pref_charge_limit}. ValueError: {ve}",
                level = 'WARNING'
            ) ### DEBUG
            SOC = self.pref_charge_limit
        except TypeError as te:
            self.ADapi.log(
                f"{self.charger} Not able to get SOC. Return value: {self.pref_charge_limit}. TypeError: {te}",
                level = 'WARNING'
            ) ### DEBUG
            SOC = self.pref_charge_limit
        except Exception as e:
            self.ADapi.log(
                f"{self.charger} Not able to calculate kWhRemainToCharge. Exception: {e}",
                level = 'WARNING'
            )
            SOC = self.pref_charge_limit
        return SOC


    def ChargeLimitChanged(self, entity, attribute, old, new, kwargs):
        try:
            self.oldChargeLimit = new
        except (ValueError, TypeError) as ve:
            self.ADapi.log(
                f"{self.charger} new charge limit: {new}. Error: {ve}",
                level = 'DEBUG'
            )
        except Exception as e:
            self.ADapi.log(
                f"Not able to process {self.charger} new charge limit: {new}. Exception: {e}",
                level = 'WARNING'
            )
        if self.getLocation() == 'home':
            if float(self.ADapi.get_state(self.battery_sensor)) > float(new):
                if self.hasChargingScheduled():
                    CHARGE_SCHEDULER.removeFromQueue(vehicle_id = self.vehicle_id)
                    self.kWhRemainToCharge = -1

            elif int(new) <= 90:
                if not self.findNewChargeTime():
                    self.stopCharging()

            elif int(new) > 90:
                self.startCharging()

    def changeChargeLimit(self, chargeLimit = 90 ):
        self.oldChargeLimit = self.ADapi.get_state(self.charge_limit)
        self.ADapi.call_service('tesla_custom/api',
            command = 'CHANGE_CHARGE_LIMIT',
            parameters = { 'path_vars': {'vehicle_id': self.vehicle_id}, 'percent': chargeLimit}
        )


class Heater:
    """ Heater
        Parent class for on_off_switch and electrical heaters
        Sets up times to save/spend based on electricity price
    """

    def __init__(self,
        api,
        heater = None,
        consumptionSensor = None,
        kWhconsumptionSensor = None,
        max_continuous_hours = 8,
        on_for_minimum = 8,
        peakdifference = 0.3,
        namespace = None,
        away = None,
        automate = None,
        recipient = None
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
        self.prev_consumption = 0
        self.max_continuous_hours = max_continuous_hours
        self.on_for_minimum = on_for_minimum
        self.peakdifference = peakdifference

            # Consumption data
        self.time_to_save:list = []
        self.time_to_spend:list = []
        self.off_for_hours:int = 0
        self.consumption_when_turned_on:float = 0.0
        self.isOverconsumption = False
        self.increase_now = False
        self.normal_power = 0
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


    def awayStateListen(self, entity, attribute, old, new, kwargs):
        if not self.namespace:
            self.away_state = self.ADapi.get_state(entity) == 'on'
        else:
            self.away_state = self.ADapi.get_state(entity, namespace = self.namespace) == 'on'
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def heater_getNewPrices(self, kwargs):
        global ELECTRICITYPRICE
        self.time_to_save = ELECTRICITYPRICE.findpeakhours(
            peakdifference = self.peakdifference,
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


    def heater_setNewValues(self, kwargs):
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
    def setPreviousState(self):
        self.isOverconsumption = False
        self.ADapi.run_in(self.heater_setNewValues, 5)


    def setSaveState(self):
        self.isOverconsumption = True
        self.ADapi.run_in(self.heater_setNewValues, 1)


    def setIncreaseState(self):
        self.increase_now = True
        self.ADapi.run_in(self.heater_setNewValues, 1)


        # Functions to calculate and log consumption to persistent storage
    def findConsumptionAfterTurnedOn(self, kwargs):
        try:
            self.consumption_when_turned_on = float(self.ADapi.get_state(self.kWhconsumptionSensor))
        except ValueError:
            self.ADapi.log(f"{self.kWhconsumptionSensor} unavailable in finding consumption", level = 'DEBUG')
        if self.findConsumptionAfterTurnedOn_Handler != None:
            if self.ADapi.timer_running(self.findConsumptionAfterTurnedOn_Handler):
                self.ADapi.log(f"Timer is running. Try cancel_timer: {self.findConsumptionAfterTurnedOn_Handler}")
                try:
                    self.ADapi.cancel_timer(self.findConsumptionAfterTurnedOn_Handler)
                except Exception as e:
                    self.ADapi.log(
                        f"Not able to stop findConsumptionAfterTurnedOn_Handler for {self.heater}. Exception: {e}",
                        level = "INFO"
                    ) ### DEBUG

        self.findConsumptionAfterTurnedOn_Handler = None
        self.ADapi.listen_state(self.registerConsumption, self.consumptionSensor,
            constrain_state=lambda x: float(x) < 20,
            oneshot = True
        )

    def registerConsumption(self, entity, attribute, old, new, kwargs):
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
                level = "INFO" ### DEBUG
            )


        # Helper functions for windows
    def windowOpened(self, entity, attribute, old, new, kwargs):
        if self.numWindowsOpened() != 0:
            self.windows_is_open = True
            self.notify_on_window_closed = True
            if self.automate:
                self.ADapi.turn_on(self.automate)
            self.ADapi.run_in(self.heater_setNewValues, 0)


    def windowClosed(self, entity, attribute, old, new, kwargs):
        if self.numWindowsOpened() == 0:
            self.windows_is_open = False
            self.notify_on_window_open = True
            self.ADapi.run_in(self.heater_setNewValues, 0)


    def numWindowsOpened(self):
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
        heater = None,
        consumptionSensor = None,
        kWhconsumptionSensor = None,
        max_continuous_hours = 8,
        on_for_minimum = 8,
        peakdifference = 0.3,
        namespace = None,
        away = None,
        automate = None,
        recipient = None,
        indoor_sensor_temp = None,
        target_indoor_temp = 23,
        rain_level = 300,
        anemometer_speed = 10,
        low_price_max_continuous_hours = 1,
        low_price_peakdifference = 1,
        windowsensors = [],
        daytime_savings = {},
        temperatures = {}
    ):

        self.indoor_sensor_temp = indoor_sensor_temp
        self.target_indoor_temp = float(target_indoor_temp)
        self.rain_level = rain_level
        self.anemometer_speed = anemometer_speed
        self.low_price_max_continuous_hours = low_price_max_continuous_hours
        self.low_price_peakdifference = low_price_peakdifference
        self.windowsensors = windowsensors
        self.daytime_savings = daytime_savings
        self.temperatures = temperatures

        super().__init__(
            api = api,
            heater = heater,
            consumptionSensor = consumptionSensor,
            kWhconsumptionSensor = kWhconsumptionSensor,
            max_continuous_hours = max_continuous_hours,
            on_for_minimum = on_for_minimum,
            peakdifference = peakdifference,
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

        self.windows_is_open = False
        for window in self.windowsensors:
            if self.ADapi.get_state(window) == 'on':
                self.windows_is_open = True

        self.notify_on_window_open = True
        self.notify_on_window_closed = False

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
    def heater_getNewPrices(self, kwargs):
        global ELECTRICITYPRICE
        super().heater_getNewPrices(0)
        self.time_to_spend = ELECTRICITYPRICE.findLowPriceHours(
            peakdifference = self.low_price_peakdifference,
            max_continuous_hours = self.low_price_max_continuous_hours
        )


        """Logging purposes to check what hours heating will be turned up"""
        #if self.time_to_spend:
        #    self.ADapi.log(f"{self.heater} Extra heating at: {ELECTRICITYPRICE.print_peaks(self.time_to_spend)}", level = 'INFO')


        # Helper function to find correct dictionary element in temperatures
    def find_target_temperatures(self):
        global OUT_TEMP
        target_num = 0
        for target_num, target_temp in enumerate(self.temperatures):
            if target_temp['out'] >= OUT_TEMP:
                if target_num != 0:
                    target_num -= 1
                return target_num

        return target_num


        # Functions to set temperature
    def setSaveState(self):
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


    def heater_setNewValues(self, kwargs):
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
        heater = None,
        consumptionSensor = None,
        kWhconsumptionSensor = None,
        max_continuous_hours = 8,
        on_for_minimum = 8,
        peakdifference = 0.3,
        namespace = None,
        away = None,
        automate = None,
        recipient = None
    ):

        self.daytime_savings = {}

        super().__init__(
            api = api,
            heater = heater,
            consumptionSensor = consumptionSensor,
            kWhconsumptionSensor = kWhconsumptionSensor,
            max_continuous_hours = max_continuous_hours,
            on_for_minimum = on_for_minimum,
            peakdifference = peakdifference,
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
        remote_start = None,
        program = None,
        running_time = 3,
        finishByHour = 6
    ):

        self.ADapi = api
        self.handler = None

        self.program = program
        self.remote_start = remote_start
        self.running_time = running_time
        self.finishByHour = finishByHour

        self.ADapi.listen_state(self.remoteStartRequested, remote_start,
            new = 'on'
        )

        if self.ADapi.get_state(remote_start) == 'on':
            self.ADapi.run_in(self.findTimeForWashing,70)


    def remoteStartRequested(self, entity, attribute, old, new, kwargs):
        self.ADapi.run_in(self.findTimeForWashing,5)


    def findTimeForWashing(self, kwargs):
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


    def startWashing(self, kwargs):
        if (
            self.ADapi.get_state(self.program) == 'off'
            and self.ADapi.get_state(self.remote_start) == 'on'
        ):
            self.ADapi.turn_on(self.program)


    def resetHandler(self):
        if self.handler != None:
            if self.ADapi.timer_running(self.handler):
                try:
                    self.ADapi.cancel_timer(self.handler)
                except Exception as e:
                    self.ADapi.log(f"Not possible to stop timer for appliance. {e}", level = 'DEBUG')
                finally:
                    self.handler = None
                    