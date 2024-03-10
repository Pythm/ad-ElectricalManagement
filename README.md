# ad-Electrical Management

> [!NOTE]
> Readme currently under construction. Some more testing before taking this out of beta.

Controls your electrical chargers, heaters and switches based on consumption/production. Uses Nordpool prices and has a maximum kWh pr hour limitation.

Currently supports controlling Tesla and Easee chargers. Charging Tesla on an Easee charger is not yet tested.

### Dependencies:
Install Nordpool custom components via HACS: https://github.com/custom-components/nordpool
Workday Sensor: https://www.home-assistant.io/integrations/workday/

### Sensors:
Consumption sensor and accumulated consumption pr hour sensor.
I recommend Tibber Pulse connected to HAN port. Check out https://tibber.com/
If you are interested in switching to Tibber you can use my invite link to get a startup bonus: https://invite.tibber.com/fydzcu9t

If you have solar or other electricity production you add a production sensor and a accumulated production sensor.

## Installation

Download the `Electricalmanagement` directory from inside the `apps` directory here to your local `apps` directory, then add the configuration to enable the `electricalManagement` module.

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
