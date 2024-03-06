# ad-Electrical Management

> [!NOTE]
> Readme currently under construction. Some more functionality to come before taking this out of beta.

Controls your electrical chargers, heaters and switches based on Nordpool price with a maximum kWh pr hour limitation.

Currently supports controlling Tesla and Easee chargers.

### Dependencies:
Install Nordpool custom components via HACS: https://github.com/custom-components/nordpool
Workday Sensor: https://www.home-assistant.io/integrations/workday/

### Sensors:
Consumption sensor and accumulated consumption pr hour sensor.
I recommend Tibber Pulse connected to HAN port. Check out https://tibber.com/
If you are interested in switching to Tibber you can use my invite link to get a startup bonus: https://invite.tibber.com/fydzcu9t

## Installation

Download the `hacs` directory from inside the `apps` directory here to your local `apps` directory, then add the configuration to enable the `hacs` module.

## App configuration

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  log: power_log
  log_level: INFO ###
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

```

key | optional | type | default | description
-- | -- | -- | -- | --
`module` | False | string | | The module name of the app.
`class` | False | string | | The name of the Class.
