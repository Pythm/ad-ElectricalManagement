
# ad-Electrical Management

> 🤖 **Set up with an AI assistant:** the [LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md) helps an AI assistant find your own entity ids in Home Assistant and write the configuration.

The purpose of this Appdaemon app is to help reduce your electricity bill by:

- Automating charging times for electric vehicles (EVs), so they charge when electricity rates are lower.
- Turning up/down heating sources and on/off hot water boilers based on electricity prices.
- Stay within hourly max kWh consumption based on grid tariffs if applicable.

---

## 🚨 Breaking Changes

### **v1.0.6** – behaviour changes (nothing to do in the default case)

No configuration changes are needed. Things that behave differently:

- **Charging reduction when over the kWh goal** now uses the correct amount: previously a reduction request made the chargers drop straight to their minimum amps, now they are lowered by the amps actually needed.
- **Charging queue priority** is now actually applied to the queue order.
- **The per-minute check runs at second 5** (not second 0) and charger reduction is skipped in minute 0 if the accumulated consumption sensor has not reset yet.
- **Stale accumulated sensor**: `reload_config_entry` is called at most every 15 minutes.
- **Heater power is set with `power` (watts) and no longer learned.** Add `power:` with the rated watts to every `climate`, `heater_switches` and `climate_commander` entry. **Action needed:** a WARNING at startup lists the heaters without it. Without `power` the stored value is kept (or 300 W for a climate, 1000 W for a switch when nothing is stored). Why: learning only ever went up, and until 1.0.6 it stored the sum of several heaters running at the same time, so stored values can be far too high (a 2 kW water heater stored as 6.7 kW). The value is used for the charge plan after save periods, the total heater power, solar overproduction, and as consumption for heaters without a consumption sensor.
- **Json storage** is written atomically, also after learning idle consumption and high usage, and an unreadable file is renamed to `*.corrupt-<time>.json` and the app starts fresh instead of failing to start.
- **Price app not ready**: if ElectricalPriceCalc has no prices yet at startup the app uses 1 hour slots and retries (for `pricearea`, the price fetch runs in the background). If `electricalPriceApp` cannot be found the app logs an error and stops.
- **Config is validated**: unknown keys are ignored, but invalid values are logged at ERROR and the app does not start. `options` is optional; `notify_receiver` can be a single name or a list.
- `get_instance()` and the singleton are removed.
- **Battery size learning** now subtracts percent from percent and kWh from kWh (it used to mix the two), so learned `battery_size` values will move a little over the next sessions.
- **Links between cars and chargers are one-to-one**: linking a car to a charger detaches any other car from that charger. A guest car switched on while a Tesla is linked to the Easee detaches the Tesla cleanly. Leftover `guest_...` queue entries are removed at startup.
- **Cars and chargers, behaviour changes**:
  - A car without a charger link is now read from its own sensor, so a Tesla that reports `NoPower` is linked to the Easee (when the Easee is `awaiting_start`) instead of being bound to its own wall connector. A Tesla charging on its own wall connector outside the planned hours is now seen (and stopped) even if it was unlinked.
  - Easee in `ready_to_charge`: the first 10 `resume` commands are sent every minute as before, then every 10 minutes with one warning and one notification.
  - Easee current sensor reading 0 while charging no longer lowers the stored amps for the first 3 minutes.
  - `WAKE_UP` is sent to a sleeping Tesla when its data is older than 12 minutes (it used to be sent only when the data was fresh). Set `wake_when_stale: false` on a car to turn this off.
  - **Unlink grace for Tesla (new optional `unlink_grace_minutes`, default 12 for Tesla cars, 0 = old behaviour)**: when a Tesla linked to an external charger (Easee) reports a state that differs from the charger, the link is no longer dropped at once. The app waits `unlink_grace_minutes`, then reads the car again and unlinks only if it still differs (a missing/`unavailable` value keeps the link). Cloud data from a sleeping Tesla is often old or wrong, and the Easee is the real sensor; real unplugs are still caught by the Easee's own `disconnected` handling. 12 matches the 11 minute default fetch interval of the Tesla integration; raise it if you slow the integration down. **Nothing to do** unless you want another value: `unlink_grace_minutes: 0` on a car restores immediate unlinking.
  - The per-minute wake-up (`wakeMeUp`) for a Tesla linked to its own charger that reports `NoPower` now runs only when it is time to charge for that car (or for solar charging); it used to run every minute as long as the car was in the charging list.
- **`low_price_max_continuous_hours` works again** (climate/heaters only, optional). The README has described it for a long time, but the setting was read and never used: spend hours always used the 3 lowest hours of the day. It now replaces that fixed 3. **Nothing to do in the default case**: without the key the behaviour is as before (3). If you already have the key in your YAML, its value is used from now on, so a value other than 3 changes the number of spend hours. Needs [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc) 0.1.8 or later; without the key an older ElectricalPriceCalc still works.
- **Guest car on the Easee** is handled by one class (`guest.py`): the guest id is now stable (`guest_<charger id>`), a guest switch that is already on when AppDaemon restarts recreates the guest, the teardown (queue, solar list, links, timers, Easee pause, switch off) runs once and in a fixed order, and guest sessions no longer raise `max_kWh_charged`. A warning and notification is sent if the guest switch is turned on while a Tesla is linked to the Easee. New optional `guest_remove_after_minutes` (default 12) sets how long the Easee may stay disconnected before the guest is removed.
- **New: `climate_commander`** (optional). Climates can now be controlled by [ClimateCommander](https://github.com/Pythm/ad-ClimateCommander) 1.4.0 or later: ElectricalManagement decides save / spend / overconsumption from prices and consumption, ClimateCommander turns that into a setpoint (heaters with an outdoor-temperature table, and HVAC with a fixed indoor temperature, also in cool mode). See [Climates controlled by ClimateCommander](#-climates-controlled-by-climatecommander-climate_commander). **Nothing to do** if you keep `climate:`.
- **`climate:` is deprecated** (a WARNING is logged at startup). It still works unchanged. Move the climate to a ClimateCommander app when convenient; the learned consumption data is kept because it is stored under the entity id.
- **`temperatures` table boundary**: an entry now applies from its `out` value and up, as documented. Before, an outdoor temperature of exactly `7.0` used the entry below `out: 7`. Only exact readings change.
- **Expected heater consumption after a save period** (used for the charge plan) now looks up the learned data by hours off. It used minutes, so it always used the longest-off data. Charge time estimates after save periods change.
- **Fire alarm**: `false-alarm` turns on only the climates and switches that ElectricalManagement turned off for the fire (before, every climate was turned on, also ones that were off). Heaters and switches now stay off until `false-alarm`; before, the periodic runner could turn a switch on again during the fire.
- **Heaters (`climate:`) and switches (`heater_switches:`), behaviour changes.** Nothing to do in the default case; the list is what a running instance will notice:
  - **`climate:` manages hvac mode `heat` only.** A climate in `cool`, `fan_only`, `dry`, `auto` or `heat_cool` gets no commands from this app at all (one WARNING per entity at the first attempt). Use a [ClimateCommander](https://github.com/Pythm/ad-ClimateCommander) app for those. `off` is handled as before.
  - **Unavailable climate or switch**: while the entity is `unavailable` or `unknown` no `set_temperature`, `turn_on` or `turn_off` is sent (one INFO line when it goes away and one when it is back). Before, the setpoint was re-sent and an unavailable switch was turned on every tick.
  - **Window temperature correction is capped at 3 degrees**, the same cap as the indoor-sensor correction, and the cap applies to the sum of the two. Example: target 23, `window_offset` -3, window sensor 35: the setpoint goes 3 down (to 20). Before, it went 15 down and ended at the vacation / minimum floor.
  - **An offline window temperature sensor** no longer lowers the setpoint: it is treated as exactly at the threshold (`target_indoor_temp + window_offset`). Before, it was compared against the heater target, which lowered the setpoint by the difference between the heater target and the indoor target.
  - **Setpoint only sent when it changes**: the new target is rounded to 0.5 before it is compared with the heater's setpoint. A target of 22.3 with the heater at 22.5 is no longer re-sent every 15 minutes. A setpoint changed by hand is still overwritten at the next run, as before.
  - **Spend periods apply at once**: when the price run (13:01, 00:05) finds a spend period that already covers now, the +1 is sent right away instead of at the next 15 minute run.
  - **Fallback temperature when nothing is configured**: a climate without `save_temp`, `save_temp_offset`, `vacation_temp` or `save` / `vacation` in `temperatures` now uses the climate's own `min_temp` attribute (5 if the entity has none) where it used a fixed 10. The same fallback is used when the heater's setpoint cannot be read during an overconsumption reduce.
  - **Vacation end** sends `hvac_mode: heat` only to a climate that this app turned off (`vacation_keep_off` or the fire alarm). A climate you turned off by hand before leaving stays off. After an AppDaemon restart during vacation, an `off` climate with `vacation_keep_off` counts as turned off by the app.
  - **Window open while the main app reduces the heater**: closing the window no longer clears the overconsumption reduction. The heater is restored when the main app releases it (within minutes), as for any reduced heater. Without a reduction, closing the window restores the normal temperature as before.
  - **"Window open" notification without an indoor temperature** (no `indoor_sensor_temp` and the climate reports no `current_temperature`) is sent without the figure; it used to print -50.
  - **`windowsensors` on a `heater_switches` entry is ignored** (one WARNING at startup). Before, an open window turned the switch off.
  - **`turn_back_on_after` is respected by the slot runner.** After the finished-appliance turn-off the switch stays off until `turn_back_on_after` seconds have passed, or until someone turns it on. With `0` the app never turns it on again. Before, the hourly runner turned every switch on again at the next full hour.
  - **`notify_when_finished` works without a turn-off window**: the notification is sent whenever the key is on; the automatic turn-off needs both `turn_off_after` and `turn_off_before`.
  - **No false "Consumption finished"**: after a fire alarm turned the switch off, and after the consumption sensor was `unavailable` in the middle of a program (a sensor that comes back low does not count as finished).
  - **Consumption measurement after a save period that ends inside a `daytime_savings` window** (nobody home) now starts 3 minutes after the daytime window ends, with the hours off counted from the start of the reduction. Before, it started 3 minutes after the save period while the heater was still reduced, so the learned value was wrong. A window that crosses midnight and ends tomorrow is measured as before.
  - **Consumption registered once**: the 20 minute check after a save period no longer registers a second sample when the consumption listener already did.
  - **Heaters without a consumption sensor that are `off`** count as 0 W in the overconsumption logic (before: their `power`), so an off boiler is not "reduced" and its watts are not added to what can be turned back on.
  - **Saved `vacation` is no longer repaired**: a persisted heater with `vacation: false` or `null` and no `vacation` key in the YAML keeps that value (before, the main vacation sensor was silently written in). Set `vacation:` on the entry if the heater should follow the vacation switch. A saved block without the key still loads.
- **Audi (still untested on a real car)**: the plug sensor is a `binary_sensor`, charging states are normalised (`charging`, `readyForCharging`, `chargePurposeReached`, `notReadyForCharging` ...), and the start/stop service uses the new optional `device_id` (falls back to `vin` with a warning). The start command is no longer repeated every 60 seconds: it is verified after `verify_minutes` (default 20) and retried at most `max_command_retries` (default 2) times, then a warning/notification. Optional `charge_power_kW` sets the assumed charging power. The Tesla start/wake loop is unchanged.

---

### **v1.0.2** – “away” → “vacation”

Renamed the **away** temperature mode to **vacation**:

| Old name | New name | Reason |
|----------|----------|--------|
| `away`   | `vacation` | Keeps terminology consistent and matches the word we actually use for the same concept |

> ⚠️ **If you’re still using the old `away` or `away_temp` key it will be ignored.**  
> Update your config to avoid unexpected behaviour.

---

#### Setting a global vacation temperature

You can set a single temperature that will use the offset from out temp:

```yaml
vacation_temp: 13
temperatures:
  - out:  3
    offset: 0.5
  - out:  7
    offset: 0
  - out: 10
    offset: -1
```

Or you can define a vacation temp for each outdoor temperature to gain more control:
```yaml
      temperatures:
        - out: -4
          normal: 20
          save: 13
          vacation: 14
```


### **1.0.0** - A complete rewrite
Please update all configurations in the breaking change upgrading to version 1.0.0 or later from earlier versions.

- **Calculation of electricityprice**
As of release 1.0.0, the calculations for electricity prices are now handled by another app. Please install the [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc) version 0.1.6 or later. Configure the ElectricalPriceCalc app and add it to `ElectricalManagement` with `electricalPriceApp` like this:

```yaml
electricalPriceApp: electricalPriceCalc # name of the app
```

The separation is to make it easier to maintain and continue development and creates posibilities to use the price calculations in other apps as well. There are some big changes under the hood, such as support for Nordpool prices every 15 minutes, and datetime objects are now timezone aware.

- **Spelling Correction**
Changed `notify_reciever` → `notify_receiver`.

- **Json Storage**
A json file will be created in `{self.AD.config_dir}/persistent/electricity/` or your defined location using the `json_path` in configuration.
The persistent data will be updated with key data and configuration of your entities.

Please use a new location or backup and delete your old json file upgrading from pre 1.0 or a beta version.

- **Definig climate and heater switches**
Defining climate and heater_switches can now only be done with the entity, not a name. Configuration with name could potentially find wrong sensors.

- **Weather sensors**
Electrical Management app is fully relying on the [ad‑Weather](https://github.com/Pythm/ad-Weather) app for weather data. No configuration for weather sensors in app anymore.
---

## 🧭 Planned Changes

I plan to make changes to integrate [ClimateCommander](https://github.com/Pythm/ad-ClimateCommander) with ElectricalManagement: ClimateCommander becomes one app per climate/heater with save/spend states, and ElectricalManagement controls those apps instead of its own `climate` entries. ElectricalManagement keeps controlling switches. Both configuration styles are planned to keep working.

---

## 📱 Supported Platforms

This app is designed to work with:

- [AppDaemon](https://github.com/AppDaemon/appdaemon)
- [Home Assistant](https://www.home-assistant.io/)

Home Assistant is a popular open-source home automation platform that offers a wide range of features and integrations with various smart home devices. If you're not already using Home Assistant, I recommend checking it out.

AppDaemon is a loosely coupled, multi-threaded, sandboxed Python execution environment for writing automation apps for various types of home automation software, including Home Assistant and MQTT.

---

## 📊 How Does It Work?

The app calculates the optimal charging time for your electric car based on your historical electricity consumption. It considers factors like weather conditions, current rates, and future prices.

For heating sources and hot water boilers, the app uses similar calculations to determine when to turn them on/off/up/down based on the lowest possible electricity rates, while still ensuring comfort needs are met.

The app can also continuously monitors your energy consumption and adjusts accordingly, helping you stay within grid tariffs.

---

### 🔔 Tips & Notes

> [!TIP]  
> I use sensors from Tibber Pulse connected to HAN port. Check out https://tibber.com/ If you're interested in changing your electricity provider to Tibber, you can ask me for a invite link to get a startup bonus.

> [!NOTE]  
> Max usage limit `max_kwh_goal` is developed according to the calculation that Norwegian Energy providers base their grid tariffs on. We pay extra for the average of the 3 highest peak loads in steps of 5-10 kWh, 10-15 kWh, etc. This should be adaptable to other tariffs with some modifications.

If you have solar or other electricity production, add a production sensor and an accumulated production sensor. The app will try to charge any cars with surplus production. If all cars have reached their preferred charge limit, it will try to spend extra on heating. The calculations also support one consumption sensor with negative numbers for production.

> [!NOTE]  
> I do not have solar panels installed and this feature is only tested with manual input of test data. Please consider this untested and report any unexpected behavior.

---

## 📦 Dependencies

You'll need to clone and configure:
- [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc)
- [ad‑Weather](https://github.com/Pythm/ad-Weather)

You only need the following optional components if they are configured in your setup. Currently supported directly in app:

- Tesla Custom Integration: [HACS Tesla integration](https://github.com/alandtse/tesla)
- Easee EV charger component for Home Assistant: [HACS Easee EV Charger integration](https://github.com/nordicopen/easee_hass)

---

## 📦 Installation and Configuration

1. `git clone` into your [AppDaemon](https://appdaemon.readthedocs.io/en/latest/) `apps` directory.
2. Add configuration to a `.yaml` or `.toml` file to enable the `ElectricalManagement` module.

Example startup configuration with suggested values:

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  electricalPriceApp: electricalPriceCalc # Your ElectricalPriceCalc app
```

---

### 🗂️ Json Storage

A json file will be created in `{self.AD.config_dir}/persistent/electricity/` or your defined location using the `json_path` in configuration.
The persistent data will be updated with key data and configuration of your entities.

> [!TIP]  
> You can check the json file for automatically found sensors for cars, chargers and heaters. The json is written during shutdown, at 14.30 and after the app has learned something new (idle consumption, high usage, heater consumption). `json_path` is the full path of the file, for example `/conf/persistent/electricity/electricalmanagement.json`.
---

### 🔌 Grid tariffs

Provide a consumption sensor `power_consumption` and an accumulated consumption per hour sensor `accumulated_consumption_current_hour` to calculate and adjust electricity usage.

> [!IMPORTANT]  
> `accumulated_consumption_current_hour` is a kWh sensor that resets to zero every hour.

Set a maximum kWh limit using `max_kwh_goal` and define a `buffer`. Buffer size depends on how much of your electricity usage is controllable, and how strict you set your max kWh usage. It defaults to 0.4 as it should be a good starting point. The top three hours is stored under `topUsage` in the json file.

> [!IMPORTANT]  
> The maximum usage limit per hour, `max_kwh_goal`, is by default 15 kWh. If the average of the 3 highest consumption hours exceeds this limit, it will increase by 5 kWh. If the limit is set too low, it may reduce heating, turn off switches, and change the charge current. Please define a proper value for `max_kwh_goal` according to your normal electricity usage.

```yaml
  power_consumption: sensor.power_home
  accumulated_consumption_current_hour: sensor.accumulated_consumption_current_hour_home
  max_kwh_goal: 15
  buffer: 0.4
```

### 🏖️ Setting Vacation Mode

Set a main `vacation` switch to lower temperature when away. This can be configured/overridden individually for each climate/switch entity if you are controlling multiple apartments, etc.

```yaml
  vacation: input_boolean.vacation
```

---

### 📌 Defining options

#### 🔌 Reducing Consumption to Stay Below Max kWh Goal

The app checks power consumptions and reacts to prevent using more than defined with `max_kwh_goal`. It reduces charging speed on car(s) currently charging to a minimum, before turning down heater_switches and climate entities. If it is still going over the app can pause charging if `pause_charging` is configured under `options`.


#### 📢 Notifications and Information

Receive notifications about charge time to your devices with `notify_receiver`. It will also notify if you left a window open and it is getting cold, or if it is getting quite hot and the window is closed if you configure windows with heaters.

You can also create and configure an Home Assistant input_text with `infotext` to display currently planned charge time in Home Assistant or some external displays.

Running multiple ElectricalManagement instances? Configure a home_name so you know where the notifications comes from.

```yaml
  notify_receiver:
    - mobile_app_yourphone
    - mobile_app_yourotherphone
  home_name: home
  infotext: input_text.information
  options:
    - notify_overconsumption
    - pause_charging
```

> [!TIP]  
> Long press notifications for options

You can also configure `electricalManagement` to use your own Notification app instead with `notify_app`. You'll need to have a function in your app to receive. App sends one call with kwargs: message, message_title, message_recipient, and also_if_not_home. Data might contain tag to replace old notifications. More kwargs can be added later or on request.

```python
def send_notification(self, **kwargs) -> None:
    """Sends notification to recipients via Home Assistant notification.
    """
    message: str = kwargs['message']
    message_title: str = kwargs.get('message_title', 'Home Assistant')
    message_recipient: str = kwargs.get('message_recipient', True)
    also_if_not_home: bool = kwargs.get('also_if_not_home', False)
    data: dict = kwargs.get('data', {})
    for re in message_recipient:
        self.ADapi.call_service(f'notify/{re}',
            title = message_title,
            message = message,
            data = data,
            namespace = self.namespace
        )
```

If you use your own app the notify_overconsumption will only notify you when home. To also receive notifications when away use `notify_overconsumption_also_when_away`

---

### 🌤️ Weather Sensors

The app is relying on the [ad‑Weather](https://github.com/Pythm/ad-Weather) app for weather data:

- It consolidates all your weather sensors into a single app.  
- It **publishes events** that other apps (like ElectricalManagement) can use.

ElectricalManagement relies on the outside temperature to log and calculate electricity usage. Climate entities set heating based on the outside temperature.

In addition you can define a rain amount `rain_level` (Defaults to 3) and wind speed `anemometer_speed` (Defaults to 40) to increase heating in the climate entities.

---

### 🧭 Namespace

A key feature of Appdaemon is the ability to define custom namespaces. Visit the [Appdaemon documentation](https://appdaemon.readthedocs.io/en/latest/CONFIGURE.html#) for more information.

If you have not configured any namespace for your HASS plugin in your `appdaemon.yaml` file, you can safely ignore namespaces.

> [!IMPORTANT]  
> As of version 0.1.5 you can set a namespace for heater/climate and charging entities with `main_namespace` if you have defined a custom HASS namespace. You can then configure the `namespace` in every charger and heater/climate that belongs to Home Assistant instances with another custom namespace if you are running multiple Home Assistant instances at home.

> :bulb: **TIP**  
> The app is designed to control electricity usage at your primary residence and will only adjust charging amps on chargers/cars that are within your home location. If you want to manage electricity consumption in other locations, I recommend setting up a separate Home Assistant and AppDaemon instance for each location.

---

### 🔄 Mode Change Events

This app listens to event `"MODE_CHANGE"` in Home Assistant. It reacts to mode `"fire"` by turning off all heaters and stopping charging, and `"false-alarm"` to revert back to normal operations.

> The use of events in Appdaemon and Home Assistant is well documented in [Appdaemon docs - Events](https://appdaemon.readthedocs.io/en/latest/APPGUIDE.html#events)

To set mode from another Appdaemon app simply use:

```python
self.fire_event("MODE_CHANGE", mode = 'your_mode_name')
```

> [!TIP]
> `ElectricalManagement` now supports the same translation on Modes as Lightwand. Check out the documentation for Lightwand in the [translation section](https://github.com/Pythm/ad-Lightwand?tab=readme-ov-file#translating-or-changing-modes) to listen for another event than `"MODE_CHANGE"` or use your own names for the pre defined mode names and change `"fire"` and `"false-alarm"` to comply with rest of your smart home. To adopt the translation, configure the `ModeTranslation` app that comes with Lightwand 2.3.0 and add `dependencies: mode_translation` to `ElectricalManagement`, so the names are loaded before this app starts (older Lightwand versions: one Lightwand room app configured with the translation settings). Without Lightwand installed the English names are used.

---


## 🔋 Charging

The app calculates electric vehicle (EV) charging time based on the State of Charge (SOC), battery size, and outside temperature.

> [!NOTE]  
> If you have other high electricity consumption in combination with a low limit, it may reduce charging down to the minimum allowed by the charger, or even stop charging if you configure with option `pause_charging`. This could result in unfinished charging if the limit is too low or consumption is too high during the calculated charge time.

The app supports controlling Tesla vehicles directly and via Easee wall chargers. There is also a possibility to configure your car with sensors.

---

#### 🔌 Customizing strict charging

The app calculates the optimal charging price and schedule based on data from [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc), ensuring a coherent time frame from start to finish. Vehicles will charge when the price is cheaper than the calculated rate. Additionally, you can customize the charging behavior by specifying a price difference between the calculated charging period with `startBeforePrice` (default 0.01) to start earlier if prices are still low, ensuring enough time to charge even with limited data for maximum kWh usage per hour. You can also force stop charging with `stopAtPriceIncrease` (default 0.3) if the charging isn't completed within the calculated time.

```yaml
  startBeforePrice: 0.01
  stopAtPriceIncrease: 0.3
```
---

#### 🔌 Setting a fixed charge price

Instead of calculating the optimal charging time, you can set a fixed price threshold for each car. When the current electricity price is below this value the car will charge. This is useful when you have a stable rate such as “Norgespris”, enabling cheaper night‑time charging.
```yaml
  charge_below_price: 0.8
```

---

### 🛠️ Configuration for Chargers

#### Home Assistant Helpers

Create Home Assistant helpers to manage charging:

1. **Guest Function**: There is a `guest` function defined with an `input_boolean` on chargers. This allows guests to charge. You will need to set up a phone to receive nofication and long press the received notification to select either charge now, or input estimated kWh to charge. When the guest switch is turned on the expected kWh starts at the value last entered for that charger (5 kWh the first time after AppDaemon starts), and a new value from the notification replaces it.

---

### 🚘 Easee Chargers

> [!IMPORTANT]  
> Locking Easee charger to 3-phase IT net (230V) requires a minimum of 11A to charge. The app can turn down charging as low as 6A, and if so, the charging will stop. To avoid this, set Phase mode to Automatic in your Easee app.

> [!NOTE]  
> Note that not all sensors are activated by default in the Easee integration. Please ensure you activate all relevant sensors to enable the full functionality of the app.

The Easee integration automatically detects wall charger information if its sensor names are in English; simply provide the name of your Easee using the `charger` option. If your sensors have names in another language, manually input the correct sensor names in the configuration. Check logs for any errors and provide missing sensors.

```yaml
  easee:
    - charger: nameOfCharger
      charger_status: sensor.nameOfCharger_status
      reason_for_no_current: sensor.nameOfCharger_arsak_til_at_det_ikke_lades
      current: sensor.nameOfCharger_strom
      charger_power: sensor.nameOfCharger_effekt
      voltage: sensor.nameOfCharger_spenning
      max_charger_limit: sensor.nameOfCharger_maks_grense_for_lader
      session_energy: sensor.nameOfCharger_energi_ladesesjon
      idle_current: switch.nameOfCharger_ventestrom
```

---

### 🚗 Configuration for Cars

#### Home Assistant Helpers

Create Home Assistant helpers to manage charging:

1. **Default Finish Time**: Charging is completed by default at 7:00 AM. To set a different hour for the charging to be finished, use an `input_number` sensor configured with `finishByHour`. This can also be configured with an integer directly in configuration if you do not intend to change it.
2. **Charge Now**: To bypass smart charging and charge immediately, configure `charge_now` as an `input_boolean`. The sensor will be automatically set to false after charging is completed or when the charger is disconnected.
3. **Charging on Solar Power**: If you generate electricity, you can choose to charge only during surplus production. Define another `input_boolean` and configure it with the `charge_on_solar` option.

#### Priority Settings for Cars

Multiple cars with priority levels 1–5 are supported by the app. The app queues the next car based on priority when there's enough power available. If multiple vehicles are in the queue, charging vehicles must reach full capacity or a minimum of 16A before the next charger starts.

Priority settings for cars include:

- Priority 1–2: These cars will continue charging until complete, regardless of any price increases due to adjusting the speed based on the consumption limit.
- Priority 3–5: These cars will stop charging at the price increase that occurs after the calculated charge time ends.

#### Battery Size
The app will calculate battery size. It defaults to 100kWh to start with. If you have a car with both `battery_sensor` that gives you SOC and a `session_energy` either from onboard charger or wall charger it will calculate your size. It needs to charge over 35% to store your size.

You can configure `battery_size` for your car, but then the calculations will be reset to that value on every restart.

> [!TIP]
> Configure the battery_size once and it will be stored to persistent data (json) and remove it so calculations can overwrite the value.

---

### 🚘 Configuring Teslas

> :warning: **WARNING**  
> It is necessary to restart the Tesla integration, and in some cases, reboot the vehicle to re-establish communications with the Tesla API after any service visits and also changes/reconfiguration of the integration in Home Assistant, such as if you need to update the API key.

Input the name of your Tesla using the `car` option. Check logs for any errors and provide missing sensors. The app will automatically find all sensors listed in the cars section below.

```yaml
  tesla:
    - car: nameOfCar
      pref_charge_limit: 90
      finishByHour: input_number.charge_finished
      priority: 3
      charge_now: input_boolean.charge_now
```

---

### 🚗 Defining Other Vehicles to Charge

Please try configure your other cars with the following sensors under `cars`:

```yaml
  cars:
    - carName: name-your-car
      charger_sensor: binary_sensor._charger
      charge_limit: number._charge_limit
      battery_sensor: sensor._battery
      asleep_sensor: binary_sensor._asleep
      online_sensor: binary_sensor._online
      location_tracker: device_tracker._location_tracker
      destination_location_tracker: device_tracker._destination_location_tracker
      arrival_time: sensor._arrival_time
      software_update: update._software_update
      force_data_update: button._force_data_update
      polling_switch: switch._polling
      data_last_update_time: sensor._data_last_update_time

      charger_switch: switch._charger
      charging_amps: number._charging_amps
      charger_power: sensor._charger_power
      session_energy: sensor._energy_added
      maxChargerAmpere: 32
      volts: 220
      phases: 3

```

In addition to the HA helpers.

> [!WARNING]  
> The default location if no sensor for location is provided, is 'home'. This will stop charging if you are controlling your car and not a wall charger if it is not charge time. Please make sure your location sensor is functioning properly. If your car does not provide a location you can use another tracker, or make sure you have configured a `charge_now` to force charging when not home.

If the sensors does not work for your car, please open an issue.

---

## 🌡️ Climates controlled by ClimateCommander (`climate_commander`)

From version 1.0.6, a climate can be configured in [ClimateCommander](https://github.com/Pythm/ad-ClimateCommander) (1.4.0 or later) and controlled from here. The work is split:

- **ClimateCommander** owns the climate: setpoint (outdoor-temperature table or fixed indoor temperature), windows, vacation, daytime savings, notifications, screens, HVAC modes (heat/cool/fan_only).
- **ElectricalManagement** owns the electricity: save and spend periods from prices, reductions when you are over the kWh goal, solar overproduction, and the learned consumption used for the charge plan.

ElectricalManagement sends one of four power states to the ClimateCommander device. What the device does with them is configured in ClimateCommander:

| State | When | Heater with a `temperatures` table | HVAC / fixed indoor temperature |
|---|---|---|---|
| `normal` | otherwise | table setpoint | follows the indoor sensor |
| `save` | inside a save period (and `automate` is on) | `save_temp_offset`, `save_temp` or the table's `save` | setpoint + `save_temp_offset` (default −0.3, opposite sign when cooling) |
| `spend` | inside a spend period, or solar overproduction | + `spend_temp_offset` (default 1) | + `spend_temp_offset` (−1 when cooling) |
| `reduce` | over the kWh goal | the lowest of the save and vacation temperature | same as `save` |

List the ClimateCommander apps under `climate_commander`, with the energy keys for each:

```yaml
  climate_commander:
    - app: heater_bathroom            # name of the ClimateCommander app
      consumptionSensor: sensor.bathroom_floor_electric_consumption_w
      kWhconsumptionSensor: sensor.bathroom_floor_electric_consumption_kwh
      max_continuous_hours: 4
      on_for_minimum: 6
      pricedrop: 0.2
      pricedifference_increase: 1.07
      priceincrease: 0.4
      low_price_max_continuous_hours: 3
      #automate: input_boolean.automate_heater   # price savings on/off, default: the main `automate`
      power: 2000                               # rated watts of the heater (set it on every entry)
      #options:
      #  - print_save_hours
    - app: climate_living_room
      climate: climate.daikin_living_room     # only needed when that app still lists several devices
```

| Key | Default | Description |
|---|---|---|
| `app` | (required) | Name of the ClimateCommander app. |
| `climate` | (optional) | Entity id. Needed only for an older ClimateCommander app with `HVAC:`/`Heaters:` lists. Without it the entry waits (up to 10 minutes) for the app to start. |
| `power` | stored value, else `300` | Rated watts of the heater. Set it: it is not learned. |
| `consumptionSensor`, `kWhconsumptionSensor` | found by name | As for `climate:`. |
| `max_continuous_hours`, `on_for_minimum`, `pricedrop`, `pricedifference_increase` | `2`, `6`, `1`, `1.07` | Save periods, see [Savings Settings](#-savings-settings). |
| `priceincrease`, `low_price_max_continuous_hours` | `1`, ElectricalPriceCalc default | Spend periods, see [Spending Settings](#-spending-settings). |
| `automate` | main `automate` | `false` or an `input_boolean` that is off: no price savings. |
| `recipient`, `options` | main values | `print_save_hours` logs the save hours. |

Notes:
- The vacation switch and `daytime_savings` are taken from the ClimateCommander device. Windows, temperatures and notifications are configured only there.
- The state is re-sent at least every 10 minutes. If ElectricalManagement stops, the device goes back to `normal` by itself after 45 minutes.
- A ClimateCommander app that is reloaded gets its state again within a minute.
- No `dependencies:` are needed between the apps.

---

## 🌡️ Climate

Here you configure climate entities that you want to control based on outside temperature and electricity price.

> [!WARNING]
> `climate:` is deprecated and logs a WARNING. It still works. New setups should configure the climate in ClimateCommander and list it under [`climate_commander`](#-climates-controlled-by-climatecommander-climate_commander). ClimateCommander accepts the same keys (`heater`, `temperatures`, `target_heater_temp`, `save_temp`, `save_temp_offset`, `vacation_temp`, `window_temp`, ...); the energy keys stay here.

> For HVAC and other climate entities that are not very power consuming, you should check out [Climate Commander](https://github.com/Pythm/ad-ClimateCommander). That app is written to keep a constant inside temperature.

Only hvac mode `heat` is managed by `climate:`. A climate in any other mode (`cool`, `fan_only`, `dry`, `auto`, `heat_cool`) gets no commands from this app; one WARNING is logged per entity. Use a ClimateCommander app for anything else.

Climate entities are defined under `climate` and you configure the temperature it should heat to, based on the outside temperature. You configure it either by `name` or with the entity ID as `heater`, and the app will attempt to find consumption sensors based on some default zwave naming, or you can define current consumption using the `consumptionSensor` for the current consumption, and `kWhconsumptionSensor` for the total kWh that the climate has used.

Set the heater's rated `power` in watts on every climate and switch (it is not learned from the consumption sensor). The app uses it to calculate how many heaters to turn down to stay below the maximum kWh usage limit, as the consumption of heaters without a consumption sensor, and together with the kWh sensor to calculate the expected available power during charging time. Without `power` a WARNING is logged and the stored value (or 300 W for a climate, 1000 W for a switch) is used.

> [!IMPORTANT]  
> If there is no kWh sensor for the heater, the calculation of needed power to reach normal operations after saving fails. The app still logs total consumption with your `power_consumption` sensor, but this does not take into account if the heater has been turned down for longer periods of time. This might affect calculated charging time.

---

### 🌡️ Temperatures

The climate is programmed to react to outdoor conditions configured with [Weather Sensors](https://github.com/Pythm/ad-ElectricalManagement?tab=readme-ov-file#weather-sensors). It's also recommended to use an additional indoor temperature sensor defined with `indoor_sensor_temp`. With that you can set a indoor target, either with `target_indoor_temp` as an integer, or `target_indoor_input` as an Home Assistant input_number helper. As of version 1.0.3 you can also set a `target_heater_temp` to set the target the heater will adjust to. This is useful if you need a higher set temperature in the heater that the room measures. This can also be set with a Home Assistant helper `target_heater_input`.

The `temperatures` dictionary consists of multiple temperature settings that adapt to the given `out`door temperature. An entry applies from its `out` value and up to the next entry; below the first entry the first one is used. Version 0.1.5 introduces additional ways to set climate temperatures.

Easiest way is to define `offset` ± degrees based on outside temperature. The offset also applies to `save_temp` and `vacation_temp`. Alternative to save temp you can define saving temperature with a `save_temp_offset` if you are using an input_number to set target.

On vacation the price savings never go below the vacation temperature (it is a floor), while spend hours still add 1 degree on top of it. Without any of `save_temp`, `save_temp_offset`, `vacation_temp`, `save` or `vacation` configured, the climate's own `min_temp` attribute is used as the fallback.

```yaml
      save_temp_offset: -0.5
      vacation_temp: 13
      temperatures:
        - out: 3
          offset: 0.5
        - out: 7
          offset: 0
        - out: 10
          offset: -1
```

If you like to have more control over the save and away temperatures you can build your dictionary this way. This includes a `normal` operations temperature, an `vacation` setting for vacations, and a `save` mode for when electricity prices are high.

```yaml
      temperatures:
        - out: -4
          normal: 20
          save: 13
          vacation: 14
```

> [!TIP]  
> To create a comprehensive temperature profile, start from your current indoor temperature and add a new dictionary entry for each additional degree adjustment required based on the outdoor temperature.

---

### 💡 Savings Settings

Savings are calculated based on a future drop in price, with the given `pricedrop`, calculating backward from the price drop to save electricity as long as the price is higher than the low price + `pricedrop` + `pricedifference_increase` increase per hour backward. 1 is no increase so to get 7% increase in pricedifference per hour configure with 1.07.

Configure `max_continuous_hours` for how long it can do savings. Defaults to 2 hours. Hot water boilers and heating cables in concrete are considered "magazines" and can be off for multiple hours before comfort is lost, so configure depending on the magazine for every climate/switch entity. You can also define a `on_for_minimum` that checks drop in price against low prices to ensure that it does not save for multiple hours without being able to heat up before prices go up again.

The app will log any saving hours when you change the any of the above. You can also configure with option `print_save_hours` in heater to print every day.
```yaml
      options:
        - print_save_hours
```
---

### 💸 Spending Settings

Spending hours occur before price increases and the temperature is increased to increase magazine energy. The amount per hour price increase to trigger this setting is defined with `priceincrease`. Additionally, `low_price_max_continuous_hours` (optional, default 3) sets how low the price must be: an hour only gets the spend setting when its price is among the `low_price_max_continuous_hours` lowest hours of the day. A lower number is stricter and gives fewer spend hours, a higher number gives more. If you are producing more electricity than you are consuming, the app will try to set spend settings on climate entities.

---

### 🏖️ Vacation State

Turns down temperature to `vacation` setting. Heater uses the default vacation switch if not configured for heater.

You can define `vacation_keep_off`in options to keep the switch / climate off during vacation.

```yaml
      options:
        - vacation_keep_off
```
---

### 🪟 Window Sensors

The app will lower the climate temperature for as long as windows are open (a window must have been open for 120 seconds; closing is reacted to at once). The lowered temperature is the lowest of the save and the vacation temperature. If the main app is reducing the heater because of overconsumption at the same time, closing the window does not end that reduction; the heater is restored when the main app releases it.

Two notifications belong to the window sensors, with these exact thresholds:
- "Window open" is sent once per opening when the indoor temperature is below `getting_cold` AND the outdoor temperature is below `getting_cold`. The threshold defaults to 18 degrees. No alert in summer, and none while the room is still warm. When no indoor temperature is known (no `indoor_sensor_temp` and the climate reports none) the text is sent without a figure.
- "Window closed" ("getting hot inside") is sent once after a window was closed when the indoor temperature is at least the indoor target plus 10 degrees and the outdoor temperature is above `getting_cold`.

`windowsensors` only applies to `climate:` entries. On a `heater_switches` entry it is ignored and a WARNING is logged at startup.

Define a window temperature sensor as `window_temp` to react quicker to sunny days and turn down heating before it gets too hot, with `window_offset` as an offset from target temperature. This is default to -3. The correction from the window sensor and from the indoor sensor together is capped at 3 degrees: a window reading 35 with target 23 and offset -3 lowers the setpoint by 3, not by 15.

---

### 🌞 Daylight Savings

The `daylight_savings` has a start and stop time. The time accepts the start time before midnight and the stop time after midnight. In addition, you can define presence so that it does not apply daylight savings.

---

### 📩 Recipients

Define custom recipients per climate or use recipients defined in the main configuration.

---

### 📄 Example Configuration

Define climate entity with `heater`.

```yaml
  climate:
    - heater: climate.floor_thermostat
      power: 1200
      consumptionSensor: sensor.floor_thermostat_electric_consumed_w_2
      kWhconsumptionSensor: sensor.floor_thermostat_electric_consumed_kwh_2
      max_continuous_hours: 2
      on_for_minimum: 12
      pricedrop: 1
      pricedifference_increase: 1.07
      #vacation: Will use apps default HA input boolean if not specified.
      automate: input_boolean.automate_heater
      #recipient: Define other recipients that configured in main configuration.
      indoor_sensor_temp: sensor.indoor_air_temperature
      window_temp: sensor.window_air_temperature
      window_offset: -3
      target_indoor_input: input_number.HA_input_number
      target_indoor_temp: 24.2
      target_heater_temp: 28
      save_temp_offset: -3
      save_temp: 12
      vacation_temp: 13
      rain_level: 3
      anemometer_speed: 40
      low_price_max_continuous_hours: 3
      priceincrease: 1
      windowsensors:
        - binary_sensor.your_window_door_is_open
      getting_cold: 20
      daytime_savings:
        - start: '07:30:00'
          stop: '22:00:00'
          presence:
            - person.wife
      temperatures:
        - out: 3
          offset: 0.5
        - out: 7
          offset: 0
```

---

## 🔌 Switches

Hot-water boilers with no temperature sensors and only an on/off switch can also be controlled using the app's functionality. It will utilize ElectricityPrice functions to find optimal times for heating or turning on the heater. If a power consumption sensor is provided, it will enable more accurate calculations to avoid exceeding the maximum usage limit in ElectricalUsage.

Define switch entity with `switch`.

```yaml
  heater_switches:
    - switch: switch.hotwater
      power: 2000
      consumptionSensor: sensor.hotwater_electric_consumption_w
      kWhconsumptionSensor: sensor.hotwater_electric_consumption_kWh
      max_continuous_hours: 8
      on_for_minimum: 8
      pricedrop: 0.3
      pricedifference_increase: 1.07
      #vacation: Will use apps default HA input boolean if not specified.
      automate: input_boolean.automate_heater
      #recipient: Define other recipients that configured in main configuration.
```

### Notify when a program is finished (dryer, washing machine)

A switch with a power sensor can also tell you when the appliance has finished, for example a dryer. It needs `consumptionSensor` in W.

```yaml
  heater_switches:
    - switch: switch.dryer
      power: 1000
      consumptionSensor: sensor.dryer_electric_consumption_w
      kWhconsumptionSensor: sensor.dryer_electric_consumption_kWh
      notify_when_finished: true
      turn_off_after: '22:00:00'
      turn_off_before: '07:00:00'
```

The appliance counts as running when the power has been above `start_threshold` for `start_duration` seconds, and as finished when it has then been below `stop_threshold` for `stop_duration` seconds. A notification with a "Turn Off" button is sent. If the program finishes between `turn_off_after` and `turn_off_before` the switch is turned off automatically, and turned on again after `turn_back_on_after` seconds. The notification works on its own: without `turn_off_after` and `turn_off_before` (or with a value that cannot be read as a time) nothing is turned off automatically. Nothing is sent when the power fell because the app itself switched the appliance off (to save electricity, or for a fire alarm), and none while the sensor is `unavailable`. A sensor that becomes `unavailable` in the middle of a program forgets that program, so it does not count as finished when it comes back low.

`turn_back_on_after` is a power cycle, not "off for the night". The default of 60 seconds turns the switch off and on again, which silences an appliance that beeps until it is powered down. For "off until the morning" use `0` (the app never turns it on again; the next person to turn it on by hand, or in Home Assistant, ends it) or a long value in seconds. While the app has turned the appliance off this way, the hourly runner that normally keeps a switch on leaves it off.

| Key | Default | Description |
|---|---|---|
| `notify_when_finished` | `false` | Turns the function on. |
| `turn_off_after` / `turn_off_before` | `22:00:00` / `07:00:00` | Window for the automatic turn-off. Both are needed for the turn-off; the notification works without them. |
| `start_threshold` / `stop_threshold` | `100` / `15` | Watts. |
| `start_duration` / `stop_duration` | `30` / `30` | Seconds the power must stay above / below the threshold. Raise `stop_duration` for appliances with long low-power phases. |
| `turn_back_on_after` | `60` | Seconds until the switch is turned on again after the app turned it off (a power cycle). `0` never turns it on again; it stays off until someone turns it on. |

These keys are saved with the switch. A key you set in the YAML replaces the saved value at the next start. Removing a key from the YAML keeps the saved value, so set `notify_when_finished: false` to turn it off. Switches that control a hot-water boiler leave `notify_when_finished` off.

---

## 📄 Contributions

Pull request against the dev branch is much appriciated.

---

## 📄 Example App Configuration

Putting it all together in a configuration. All numbers are default values

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  dependencies:
    - electricalPriceCalc
  electricalPriceApp: electricalPriceCalc

  # Control maximum electricity usage
  power_consumption: sensor.power_home
  accumulated_consumption_current_hour: sensor.accumulated_consumption_current_hour_home
  max_kwh_goal: 15
  buffer: 0.4

  # Set home in vacation mode and turn down temperatures
  vacation: input_boolean.vacation

  # Notification options
  notify_receiver:
    - mobile_app_yourphone
    - mobile_app_yourotherphone
  infotext: input_text.information
  home_name: home

  # Configuration options
  options:
    - pause_charging
    - notify_overconsumption

  # Cars and Chargers
  startBeforePrice: 0.01
  stopAtPriceIncrease: 0.3

  tesla:
    - charger: yourTesla
      pref_charge_limit: 90
      finishByHour: input_number.finishChargingAt
      priority: 3
      charge_now: input_boolean.charge_Now

  easee:
    - charger: nameOfCharger
      reason_for_no_current: sensor.nameOfCharger_arsak_til_at_det_ikke_lades
      current: sensor.nameOfCharger_strom
      charger_power: sensor.nameOfCharger_effekt
      voltage: sensor.nameOfCharger_spenning
      max_charger_limit: sensor.nameOfCharger_maks_grense_for_lader
      session_energy: sensor.nameOfCharger_energi_ladesesjon
      idle_current: switch.nameOfCharger_ventestrom
      guest: input_boolean.easeelader_guest_using

  # Climate
  climate:
    - heater: climate.floor_thermostat
      power: 1200
      consumptionSensor: sensor.floor_thermostat_electric_consumed_w_2
      kWhconsumptionSensor: sensor.floor_thermostat_electric_consumed_kwh_2
      max_continuous_hours: 2
      on_for_minimum: 12
      pricedrop: 1
      pricedifference_increase: 1.07
      #vacation: Will use apps default HA input boolean if not specified.
      automate: input_boolean.automate_heater
      #recipient: Define other recipients that configured in main configuration.
      indoor_sensor_temp: sensor.indoor_air_temperature
      window_temp: sensor.window_air_temperature
      window_offset: -3
      target_indoor_input: input_number.HA_input_number
      target_indoor_temp: 23
      save_temp_offset: -3
      save_temp: 12
      vacation_temp: 13
      rain_level: 3
      anemometer_speed: 40
      low_price_max_continuous_hours: 3
      priceincrease: 1
      windowsensors:
        - binary_sensor.your_window_door_is_open
      getting_cold: 20
      daytime_savings:
        - start: '07:30:00'
          stop: '22:00:00'
          presence:
            - person.wife
      temperatures:
        - out: -4
          offset: 2
        - out: -3
          offset: 1
        - out: 2
          offset: 0
        - out: 7
          offset: -2
        - out: 11
          offset: -3
        - out: 14
          offset: -5
        - out: 17
          offset: -6

  heater_switches:
    - switch: switch.hotwater
      power: 2000
      consumptionSensor: sensor.hotwater_electric_consumption_w
      kWhconsumptionSensor: sensor.hotwater_electric_consumption_kWh
      max_continuous_hours: 8
      on_for_minimum: 8
      pricedrop: 0.3
      pricedifference_increase: 1.07
      #vacation: Will use apps default HA input boolean if not specified.
      automate: input_boolean.automate_heater
      #recipient: Define other recipients that configured in main configuration.
```

---

## 📄 Still got questions?

Let me know so I can update this readme

---