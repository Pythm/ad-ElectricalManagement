# ad-Electrical Management

> [!NOTE]
> Readme currently under construction.

Controls charging, climate entities and switches to control power consuming equipment like hot-water boilers etc, based on consumption/production and prices from Nordpool.

> [!TIP]
> This Appdaemon app is intended for use with Home Assistant at your home location and will only change charging amps on chargers/cars that are home. If you want to control electricity usage other places I recomend creating a Home Assistant and Appdaemon pr place.

I use sensors from Tibber Pulse connected to HAN port.
Check out https://tibber.com/ If you are interested in switching to Tibber you can use my invite link to get a startup bonus: https://invite.tibber.com/fydzcu9t


## Installation
Download the `Electricalmanagement` directory from inside the `apps` directory here to your local `apps` directory, then add the configuration to enable the `electricalManagement` module.

### Dependencies:
Install Nordpool custom components via HACS: https://github.com/custom-components/nordpool
Workday Sensor: https://www.home-assistant.io/integrations/workday/

Uses Met.no if you do not configure an outside temperature: https://www.home-assistant.io/integrations/met/

Other only needed if configured:
Tesla Custom Integration via HACS: https://github.com/alandtse/tesla
Easee

## Control of your electricity usage
Fetches prices from [Nordpool integration](https://github.com/custom-components/nordpool) to calculates savings and spend hours for heaters in addition to charging time.

Set a max kWh limit with `max_kwh_goal` and input your `buffer` to be on the safe side. Buffer size depends on how strict you want to limit usage and how much of your electricity usage is controllable.
Max usage limit during one hour increases by 5 kWh if average of the 3 highest consumption hours is over limit.
If limit is set to low it will turn down heating including switches and reduce charging to 6 Ampere before it breaks limit 3 times and raises it by 5 kWh.
Max usage limit is developed according to the new calculation that Norwegian Energy providers base their grid tariffs on but should easily be adoptable to other countries with some rewrite. 

> [!TIP]
> If you live in a country where there is no tariff on higher usage I would set limit to the same size as your main fuse in kWh.

Provide a consumption sensor and an accumulated consumption pr hour sensor to calculate electricity usage to stay within a preferred kWh limit. 
If you have solar or other electricity production you add a production sensor and a accumulated production sensor. The app will the try to charge any cars with the surplus production.

## Charger
Calculates time to charge car based on battery size and charger data. App will also register electricity usage from heaters and other based on outside temperature to better calculate time needed to charge.
Multiple cars with priority 1-5 is supported. Queues after priority.
If you have other high electricity consumption in combination with low limit it will turn down charging but no lower than 6 Amp to stay within given consumption limit.
This may result in unfinished charging if limit is too low or consumption is too high during calculated charge time.

Priority settings for charger:
1: Will start at calculated time even if staying below consumption limit will result in heaters turning down/off. Will also charge until full even if it is not complete due to turning down speed based on consumption limit.
2-5: Will wait to start charging until it is 1,6kW free capacity. Will also stop charging at price increase after calculated charge time ends. If multiple chargers apply, first will have to charge at full capacity before next charger checks if it is 1,6kW free capacity to start.

Currently supports controlling Tesla and Easee chargers. Charging Tesla on an Easee charger is not yet tested.

## Climate
Heating sources you wish to control that sets the temperature based on outside temperature, electricity price and with possibility to reduce temporarily when consumption is high. 


## Switches
Dumb hot-water boilers with no temperature sensors and only a on/off switch. It will use functions from ElectricityPrice to find times to heat/turn on. If power consumption sensor is provided it will also be able to calculate better how to avoid max usage limit in ElectricalUsage.


## App configuration

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  log_level: WARNING ### Set to INFO for more logging. 
  json_path: /conf/apps/ElectricalManagement/ElectricityData.json
  nordpool: sensor.nordpool_kwh_bergen_nok_3_10_025
  daytax: 0.4738
  nighttax: 0.3558
  power_support_above: 0.9125 # Inkl vat
  support_amount: 0.9 # 90%
  workday: binary_sensor.workday_sensor
  power_consumption: sensor.power_hjemme
  accumulated_consumption_current_hour: sensor.accumulated_consumption_current_hour_hjemme
  max_kwh_goal: 10
  buffer: 0.3
  outside_temperature: sensor.netatmo_out_temperature
  rain_sensor: sensor.netatmo_regnsensor_rain
  anemometer: sensor.netatmo_anemometer_wind_strength
  away_state: input_boolean.vacation
  notify_receiver:
    - mobile_app_your_phone

  tesla:
    - charger: yourTesla
      pref_charge_limit: 90
      battery_size: 100
      finishByHour: input_number.finishChargingAt
      priority: 3
      charge_now: input_boolean.charge_Now
      electric_consumption: sensor.tesla_electric_consumption_w
      departure: input_datetime.departure # Not implemented yet
    - charger: yourOtherTesla
      pref_charge_limit: 70
      battery_size: 80
      finishByHour: input_number.yourOtherTesla_finishChargingAt
      priority: 4
      charge_now: input_boolean.yourOtherTesla_charge_Now
      electric_consumption: sensor.tesla_electric_consumption_w
      departure: input_datetime.departure_yourOtherTesla

  easee:
    - charger: leia
      device_id: # 32 char ID string. Documentation to come
      reason_for_no_current: sensor.leia_arsak_til_at_det_ikke_lades
      current: sensor.leia_strom
      charger_power: sensor.leia_effekt
      voltage: sensor.leia_spenning
      max_charger_limit: sensor.leia_maks_grense_for_lader
      online_sensor: binary_sensor.leia_online
      session_energy: sensor.leia_energi_ladesesjon
      namespace: hass_leil
      finishByHour: input_number.easeelader_finishChargingAt
      priority: 2
      charge_now: input_boolean.easeelader_charge_Now
      guest: input_boolean.easeelader_guest_using

  heater_switches: # Used for dumb Hotwater boilers and other things to turn off during expencive hours
    - name: vvb_hus
#      switch: switch.vvb_hus
#      consumptionSensor: sensor.vvb_hus_electric_consumption_w
#      away_state: input_boolean.vekk_reist
      peakdifference: 0.12
      max_continuous_hours: 15
      on_for_minimum: 8
    - name: vvb_leilighet
      namespace: hass_leil
      away_state: input_boolean.leil_vekk_reist
      peakdifference: 0.12
      max_continuous_hours: 15
      on_for_minimum: 8

  climate:
    - name: floor_thermostat
    #- heater: climate.floor_thermostat
    #  consumptionSensor: sensor.floor_thermostat_electric_consumed_w_2
    #  kWhconsumptionSensor: sensor.floor_thermostat_electric_consumed_kwh_2
      max_continuous_hours: 14
      on_for_minimum: 6
      peakdifference: 0.15
      #away_state: Will use default if not specified.
      automate: input_boolean.automatiser_varmekabler_bod
      #recipient:
      indoor_sensor_temp: sensor.bod_fryseskap_air_temperature
      target_indoor_temp: 20
      low_price_max_continuous_hours: 3
      low_price_peakdifference: 0.65
      temperatures:
        - out: -4
          normal: 20
          spend: 21
          save: 13
          away: 14
        - out: -3
          normal: 19
          spend: 21
          save: 12
          away: 14
        - out: 2
          normal: 18
          spend: 20
          save: 11
          away: 13
        - out: 7
          normal: 16
          spend: 18
          save: 11
          away: 13
        - out: 11
          normal: 15
          save: 10
          away: 12
        - out: 14
          normal: 13
          save: 10
          away: 12
        - out: 18
          normal: 12
          save: 10
          away: 12
```

key | optional | type | default | description
-- | -- | -- | -- | --
`module` | False | string | | The module name of the app.
`class` | False | string | | The name of the Class.
