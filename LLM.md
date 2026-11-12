# ad-ElectricalManagement: notes for an AI assistant

Read this with [README.md](README.md). The workflow for finding the user's real entity ids (token, `ha_snapshot.py`, asking when several match) is in the [Pythm_AppdaemonApps LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md). Follow its rules: use ids from the index only, ask when several match, one app per `apps.yaml` edit, back up first, no commit or push unasked. The code is the reference; where this file and the README differ, this file follows the code.

## Finding the user's entities (short version; the full workflow is in the guide above)

1. The user creates a long-lived access token in Home Assistant (profile, Security tab) and saves it in a plain-text file `.local/ha_token` in the Pythm_AppdaemonApps folder. The assistant never opens or prints that file and never asks for the token in chat.
2. `python3 tools/ha_snapshot.py --init` creates the file; after the user has filled it in, `python3 tools/ha_snapshot.py --url http://<ha-address>:8123` writes `.local/ha_index.jsonl` (one entity per line: id, name, room, integration, device class, unit, state). It only reads.
3. Search that index for the entity types in the table below. Use only ids found there. When several entities match, ask the user which one (and how it is used) instead of choosing. Without a token, ask the user for the ids.
4. Write one app at a time into `apps.yaml` after backing up the file, and check the app log for WARNING/ERROR lines.

## What the app does

One app per home (`module: electricalManagement`, `class: ElectricalUsage`). It **acts on its own** every minute, so the user must say yes to each part before it is enabled:

- **Cars and chargers:** plans charging in the cheapest hours from ElectricalPriceCalc and starts, stops and sets the amps of Tesla (Tesla custom integration), Easee and generic cars. Audi (audiconnect) is work in progress and untested on a real car.
- **Hourly kWh cap:** keeps the hour under `max_kwh_goal`. It lowers charger amps first, then reduces heaters and switches, and with `options: [pause_charging]` it stops charging.
- **Heaters and switches:** save periods before a price drop and spend periods before a rise. `climate_commander:` entries get a power state that the ClimateCommander app turns into a setpoint. Deprecated `climate:` entries get a setpoint from this app. `heater_switches:` are switched on and off.
- **Fire alarm:** `MODE_CHANGE` with mode `fire` stops charging and turns off heaters and switches. A car that is not home gets no command.
- **Learning:** idle consumption, the top 3 hours, battery size and heater consumption after save periods are stored in a JSON file (`json_path`, default `<config_dir>/persistent/electricity/electricalmanagement.json`; it is a file path, not a folder).

Needs [ElectricalPriceCalc](https://github.com/Pythm/ElectricalPriceCalc) (required) and [ad-Weather](https://github.com/Pythm/ad-Weather) (`WEATHER_CHANGE` gives the outdoor temperature; without it the last stored value is used, 10 at first start).

## Config (apps.yaml)

```yaml
electricity:
  module: electricalManagement
  class: ElectricalUsage
  dependencies:                   # only names of apps that exist in apps.yaml
    - electricalPriceCalc
    - notify_mobiles
  electricalPriceApp: electricalPriceCalc     # required, the exact app name
  power_consumption: sensor.house_power
  accumulated_consumption_current_hour: sensor.house_energy_this_hour
  max_kwh_goal: 10
  vacation: input_boolean.vacation
  notify_app: notify_mobiles
  notify_receiver: [Me]
  climate_commander:
    - app: heater_bathroom        # name of a ClimateCommander app
      power: 1500                 # rated watts, from the sensor history or the user
      kWhconsumptionSensor: sensor.bathroom_floor_energy
      max_continuous_hours: 4
  heater_switches:
    - switch: switch.water_heater
      power: 2000
      consumptionSensor: sensor.water_heater_power
      max_continuous_hours: 8
      on_for_minimum: 8
```

The top-level keys are validated by `ElectricalManagementConfig` in `pydantic_models.py`. A wrong type gives `Configuration error in '<key>'` at ERROR and the app does not start. Unknown top-level keys are ignored. The list entries are read as plain dicts, so a misspelled key in an entry is silently ignored.

| Key | Type | Default | Notes |
|---|---|---|---|
| `electricalPriceApp` | str | required | Name of the ElectricalPriceCalc app. |
| `main_namespace` | str | `default` | HA namespace for sensors, events and notifications. Each list entry may set its own `namespace`. |
| `notify_app` | str | none | App with `send_notification(...)`, normally `notify_mobiles`. Without it: HA `notify/<receiver>` services. |
| `notify_receiver` | str or list | `[]` | Person labels (with `notify_app`) or notify service names (without). |
| `home_name` | str | `home` | Label in notifications only. The car location is always compared with the zone `home`. |
| `json_path` | str | see above | Full path of the JSON file. Missing folders are created. A second instance of the app needs its own file. |
| `power_consumption` | entity | none | House power in W. Without this key or the next key: no kWh cap, the queue is checked every 10 min. Both are needed for the cap. |
| `accumulated_consumption_current_hour` | entity | none | kWh this hour, resets to 0 every hour. |
| `power_production`, `accumulated_production_current_hour` | entity | none | Solar (untested with real data). |
| `max_kwh_goal` | float | 15 | Hourly cap in kWh. The cap in use is stored in the JSON and rises by 5 when the average of the top 3 hours exceeds it. |
| `buffer` | float | 0.4 | kWh kept free under the cap. |
| `options` | list | `[]` | `notify_overconsumption`, `notify_overconsumption_also_when_away`, `pause_charging`. Others: WARNING. |
| `automate` | bool or entity | `true` | Default `automate` for every heater entry. |
| `vacation` | entity | `input_boolean.vacation` if it exists | Main vacation switch. `away_state` is an older name; if both are set, `away_state` wins. |
| `infotext` | entity | none | `input_text` that shows the plan: `Car A, Car B 01:00-04:00; Car C waiting for prices; Charge below 0.8: Car D`, or `No planned charging` with no car in the queue. Cut with `…` at the helper's `max` (a new helper holds 100, tell the user to raise it to 255 with several cars). Notifications to `notify_receiver` do not need it. |
| `startBeforePrice`, `stopAtPriceIncrease` | float | 0.01, 0.3 | Charging window margins. |
| `tesla`, `audi`, `cars`, `easee`, `climate`, `climate_commander`, `heater_switches` | list of dicts | `[]` | See below. |

### Car entries (`tesla`, `audi`, `cars`)

- `tesla`: `car` (alias `charger`) is the name in the Tesla entity ids. Without it the name is taken from `charger_sensor`. Entities are found as `<domain>.<car><suffix>`, and a key in the entry overrides that: `charger_sensor` (binary_sensor `_charger`), `charge_limit` (number `_charge_limit`), `battery_sensor` (`_battery`), `asleep_sensor`, `online_sensor` (its `id` attribute is the vehicle id), `location_tracker`, `destination_location_tracker`, `arrival_time`, `software_update`, `force_data_update`, `polling_switch`, `data_last_update_time`. The charger part: `charger_switch` (switch `_charger`), `charging_amps` (number `_charging_amps`), `charger_power`, `session_energy` (`_energy_added`). Also `wake_when_stale` (true) and `unlink_grace_minutes` (12, 0 = unlink at once). A value that is not a boolean (`true`/`false`, `yes`/`no`, `on`/`off`, `1`/`0`) or not a number gives an ERROR naming the key and the default is used; an empty key gives the default.
- `audi` (WORK IN PROGRESS, untested on a real car: say so, never call it supported; ask first, see below). Needs the audiconnect custom integration (checked against its complete 2.5.2 source; nothing has run on a real car). `car` is the car's slug in the audiconnect entity ids: the integration names its device after the vehicle title (the nickname from the myAudi app, else the model short name) and every entity id is `<domain>.<title slug>_<entity name slug>`; or give `plug_state` and the name is taken from it. Found as `<domain>.<car><suffix>` unless given; the suffixes are the slugs of the entity names in the integration source, so check the `car` slug against the index: `plug_state` (binary_sensor `_plug_state`, "Plug state", `on` when the car's plugConnectionState is not `invalid` or `disconnected`, `unavailable` when the car did not report it), `charging_state` (sensor `_charging_state`, "Charging state", the car's raw text copied unchanged; the integration does not list the values, see below), `charging_power` (sensor `_charging_power`, "Charging power", kW; alias of `charger_power`), `charge_limit` (number `_current_location_charge_target`, "Current location charge target", 20-100 in steps of 5, the app rounds to that; not `number.<car>_global_charge_target`, which audiconnect says does not stop a charge, and not the read-only `sensor.<car>_target_state_of_charge`), `battery_sensor` (sensor `_state_of_charge`, %), `data_last_update_time` (sensor `_last_update`, "Last Update", the newest timestamp the car reported), `location_tracker` (device_tracker `_position`, "Position", its `vin` attribute is the vehicle id; the integration creates it only when the car reports a position, and without it the car always counts as home and the car name is the id), `charge_mode` (sensor `_charging_mode`, "Charging mode", optional and not saved: the mode of the charge in progress; `timer` gives one INFO line and no action). `device_id`: the Home Assistant device id of the car (not an entity id; ask the user). Required for any command: without it one WARNING at startup and no start or stop is sent; a wrong one is reported by the integration in the Home Assistant log (`No VIN found for device ...`), not by the app. Commands are `audiconnect.execute_vehicle_action` with `device_id` and `action: start_charger` / `stop_charger`; the service has no `vin` field. `verify_minutes` (20), `max_command_retries` (2), `charge_power_kW` (no ampere control: the Audi draws what the EVSE gives; with it `volts` and `phases` give the amps estimate, without it 16 A at 230 V is assumed). The integration polls the cloud every `scan_interval` minutes (minimum 15), so charging state and power can be that old; after a charger command it refreshes the cloud data itself about 5 s later. The app never calls `refresh_vehicle_data` or `refresh_cloud_data` and never presses `button.<car>_refresh_vehicle_data`: a vehicle refresh counts against Audi's daily API limit (`sensor.<car>_api_requests_remaining` shows what is left). On an Easee the Audi is linked like a `cars:` car (plug `on`, Easee `awaiting_start`) and gets the same link notification (normally "Charge now" only, because `charge_limit` and `battery_sensor` are found). Stop is `stop_charger` only: the app does not stop an Audi by lowering its charge target, on purpose, because the car could be left with a very low limit after the owner drives off. Do not add that. Charging state strings (`AUDI_STATE_EXACT` in `electrical_chargers.py`) are third-party evidence for the VW-group API, not confirmed on an Audi: `charging` = Charging; `readyForCharging` = Stopped; `notReadyForCharging` = NoPower with the plug `on`, Disconnected with it `off`, no change while it is `unavailable`; `chargePurposeReached`, `chargePurposeReachedAndNotConservationCharging`, `chargePurposeReachedAndConservation` and `conservation` = Complete (`conservation` gives one INFO); `error` = Stopped (one WARNING, no action on the error); `unsupported` and any other value = no change (one WARNING naming it). Logs for checking a real install: at every start one INFO `<car>: Audi entities: ...` with the entity ids the app resolved and the raw plug and charging state; at each Easee link one INFO `<easee>: <car> linked. Plug sensor ...` with the raw plug state and the raw charging state; at the first stop sent one WARNING `Audi stop is not confirmed to work with audiconnect ...`. Verified in the integration source: the entity names and the id rule, the plug sensor semantics, the `vin` attribute, the services and their fields, the refresh after an action, and that an entity reads `unavailable` when the car does not report its value. Open, not verifiable from the source: (1) the charging state strings the Audi really returns, and the "Charging mode" values (`timer` is the word the integration's `set_charge_mode` service uses), (2) whether `start_charger` and `stop_charger` do anything (the audiconnect README says a confirmed stop did not stop the charge), (3) what AppDaemon hands the service `callback` when the integration rejects a command (logged at DEBUG as `audiconnect action result`).
- `cars` (generic): `carName` is required (WARNING and skipped without it), the same sensor keys as Tesla (found as `<domain>.<carName><suffix>`), plus `maxChargerAmpere` (32), `volts` (220), `phases` (1), `min_ampere` (6), `idle_current`, `guest`. `guest` here has no disconnect timer as on the Easee, so that session ends only when the switch is turned off. Without `location_tracker` the car always counts as home. `charger_sensor` is the plug sensor (`on` = cable in); it is also how the car is linked to an Easee: in `awaiting_start`, car home, plug `on`, not charging on its own charger (`charger_switch` on), not linked to another Easee, on data newer than the cable event, and only when it is the one car that matches and no car matches on `NoPower`. Without `charger_sensor` the car is never linked to an Easee (guest switch only). At each new link to an Easee the guest notification is sent for the car: "Charge <car> Now", plus "Input expected kWh to charge" unless the car has both `charge_limit` and `battery_sensor`.
- Every car: `battery_size` (100, learned; a configured value replaces the learned one at every start), `pref_charge_limit` (90), `charge_below_price` (0 = off, else charge whenever the price is below it), `priority` (3; 1 and 2 finish charging after the price rises), `finishByHour` (7, an integer or an `input_number`), `charge_now` and `charge_only_on_solar` (false or an `input_boolean`), `namespace`. `departure` is read but not implemented: do not add it.

### `easee`

`charger` (the name in `sensor.<charger>_status`; without it, derived from `charger_status`). `status` or `charger_status` is the status sensor (= `charger_sensor`), `current` = `charging_amps`. Found automatically if not given: `_status`, `_reason_for_no_current`, `_current`, `_power`, `_energy_added`, `_voltage`, `_max_charger_limit`, `_idle_current` (keys `reason_for_no_current`, `charger_power`, `session_energy`, `voltage`, `max_charger_limit`, `idle_current`). Optional `guest` (`input_boolean`), `guest_remove_after_minutes` (12; a whole number >= 1, else WARNING and 12; minutes `disconnected` before a guest or a queued car that did not move to its own charger is removed), `namespace`. If both `status` and `charger_status` are given, `status` is used. `guest` is read only the first time the charger is saved in the JSON (see Saved values below).

### Heater entries (`climate_commander`, `heater_switches`, deprecated `climate`)

Identity: `climate_commander` needs `app` (the CC app name; `climate`, alias `heater`, only for an old CC app with `HVAC:`/`Heaters:` lists). `heater_switches` needs `switch`. `climate` needs `heater` (an entity id; `name` no longer works). Energy keys, shared by all three:

| Key | Default | Notes |
|---|---|---|
| `power` | stored value, else 300 (climate, CC) / 1000 (switch) | Rated watts. ALWAYS set; find from the device's power sensor history, else ask. |
| `consumptionSensor` | `sensor.<name>_electric_consumption_w` or `_electric_consumed_w` if it exists | W sensor of this device only. |
| `kWhconsumptionSensor` | `..._electric_consumption_kwh` or `_electric_consumed_kwh` | Needed to learn the consumption after a save period. |
| `max_continuous_hours`, `on_for_minimum` | 2, 6 | Save period length and check against low prices. |
| `pricedrop`, `pricedifference_increase` | 1, 1.07 | When a save period is found. |
| `priceincrease`, `low_price_max_continuous_hours` | 1, ElectricalPriceCalc default (3) | Spend periods; `climate` and `climate_commander` only. `low_price_max_continuous_hours` needs ElectricalPriceCalc 0.1.8; with an older one, one WARNING per app and the default 3 is used. |
| `automate` | top-level `automate` | `false` or an `input_boolean` that is off: no save periods. Spend and the kWh cap still apply. |
| `vacation` | main vacation switch (CC: the CC app's vacation switch) | An entity id string, or leave it out. |
| `recipient` | `notify_receiver` | Used by `climate` window alerts and switch "finished" alerts. |
| `options` | `[]` | `print_save_hours`; `vacation_keep_off` (only `climate` and `heater_switches`; for CC set it in the CC app). |
| `namespace` | `main_namespace` | |

`heater_switches` only: `notify_when_finished` (false), `notify_title` / `notify_message` (own text; `{name}` = friendly name, `{heater}` = entity id; defaults: entity id / `Consumption finished`; ask which language and wording the user wants), `turn_off_after` / `turn_off_before` (`'22:00:00'` / `'07:00:00'`, quote them), `start_threshold` / `stop_threshold` (100 / 15 W), `start_duration` / `stop_duration` (30 / 30 s), `turn_back_on_after` (60 s: a power cycle; 0: stays off until someone turns it on). On vacation a switch is on mainly in the cheapest 2 h of the day (off all the time with `vacation_keep_off`).

`climate` only (deprecated, do not write in new configs): `indoor_sensor_temp`, `target_indoor_input`, `target_indoor_temp` (23), `target_heater_input`, `target_heater_temp` (23), `window_temp`, `window_offset` (-3), `save_temp_offset`, `save_temp`, `vacation_temp`, `rain_level` (3), `anemometer_speed` (40), `getting_cold` (18), `windowsensors`, `daytime_savings`, `temperatures`. ClimateCommander accepts the same keys.

Saved values: for heater and car entries, a key in the YAML replaces the saved value at the next start, but removing a key keeps the saved value (also `vacation`, and `vacation_keep_off` once set through `options`). To turn something off, set it explicitly (for example `notify_when_finished: false`). Top-level defaults (`automate`, `vacation`, `notify_receiver` as `recipient`) are copied into a heater entry only when it is first saved. A later change at the top level does not reach it unless the entry sets the key itself. A value of the wrong type for a saved key is ignored with a WARNING, and the saved value stays.

Tesla and Easee chargers: `guest`, `min_ampere`, `maxChargerAmpere`, `volts` and `phases` are read only when the charger is first saved, and a later YAML value has no effect. This is on purpose (the Tesla integration can report wrong values) and fits identical chargers. To change one, the user stops the app and deletes that charger's entry under `charger` in the JSON (follow the rule on editing the JSON at the end of this file).

## Which entities fit each key

| Key | Entity | Hints when searching the index |
|---|---|---|
| `power_consumption` | `sensor`, device_class `power`, unit `W` | Whole house (HAN reader, Tibber Pulse). Not a single device. |
| `accumulated_consumption_current_hour` | `sensor`, device_class `energy`, unit `kWh` | Must reset every hour. A daily or total meter is wrong. |
| `power_production`, `accumulated_production_current_hour` | as above | Solar inverter or a meter with export. |
| `vacation`, `automate`, `charge_now`, `charge_only_on_solar`, `guest` | `input_boolean` | Helpers: if missing, tell the user what to create. |
| `finishByHour` | `input_number` (or an integer) | |
| `infotext` | `input_text` | |
| `tesla` `car` | the slug in `binary_sensor.<car>_charger` | `"integration":"tesla_custom"`. Two Teslas: one entry each. |
| `easee` `charger` | the slug in `sensor.<charger>_status` | `"integration":"easee"`. Enable the disabled Easee sensors in HA first. |
| `audi` keys | `"integration":"audiconnect"` | Work in progress. `<car>` is the slug of the vehicle title (nickname or model). `binary_sensor.<car>_plug_state`, `sensor.<car>_charging_state`, `sensor.<car>_charging_power`, `sensor.<car>_state_of_charge`, `sensor.<car>_last_update`, `number.<car>_current_location_charge_target`, `device_tracker.<car>_position` (only when the car reports a position), optional `sensor.<car>_charging_mode`. `device_id` is the HA device id, not an entity. |
| `climate_commander` `app` | an app name in `apps.yaml` | Not an entity. Check the CC app exists. |
| `heater_switches` `switch` | `switch.*` | Water heater relay, smart plug of a dryer, etc. |
| `consumptionSensor` | `sensor`, device_class `power`, unit `W` | Same device as the heater or switch. |
| `kWhconsumptionSensor` | `sensor`, device_class `energy`, `total_increasing` | Same device. |
| `indoor_sensor_temp`, `window_temp` | `sensor`, device_class `temperature` | In that room. |
| `windowsensors` | `binary_sensor`, device_class `window`/`door`/`opening` | `climate:` only. |
| `daytime_savings` `presence` | `person.*` | |

## ALWAYS ask the user

- **Rated watts for `power:`** on every `climate`, `heater_switches` and `climate_commander` entry. Find them yourself first: if the device has its own power sensor (the `consumptionSensor`, a W sensor), read its history read-only with `tools/ha_peak_power.py` from the [Pythm_AppdaemonApps](https://github.com/Pythm/Pythm_AppdaemonApps) repository (the same clone that holds `tools/ha_snapshot.py` and the `.local/ha_token` file from the [LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md); if the user has not cloned it yet, `git clone https://github.com/Pythm/Pythm_AppdaemonApps.git`, or download the single file from its `tools` folder): `python3 tools/ha_peak_power.py <sensor> ...` (it uses the user's HA token from `.local/ha_token`, which only the script reads, and prints max, p99 and the on-plateau of the last 30 days). Use the plateau when it is clear (several samples, max close to the plateau) and write the highest value rounded to 10 W. Tell the user which value you took and from which sensor, so it can be corrected. Only ask the user (nameplate, manual, installer) when there is no sensor, no history, the device never ran in the period, or the plateau is not clear. Never use the old stored `normal_power` from the JSON, EM's learned consumption, or a guess: the app no longer learns it, the old learning only went up, and until 1.0.5 it stored the sum of several heaters. Without `power:` the stored value (or 300 / 1000 W) is used and one WARNING lists the entries.
- **Two sensors of one kind** (two power meters, two temperature sensors in a room, two hourly kWh sensors): is the second a backup, a sensor for another side of the house, or something else? Never decide from names or areas.
- **Who gets notifications, and how.** (1) With `notify_app: notify_mobiles` (check the real app name), `notify_receiver` and `recipient` are the **person labels** of that app. (2) Without `notify_app`, they are HA notify service names such as `mobile_app_phone`. Never mix the two kinds. Ask whether overconsumption alerts are wanted (`notify_overconsumption`), and also when away.
- **Which car can use which charger.** Never assume a car only uses its own charger: either Tesla may charge on either Easee or wall connector. The app links cars and chargers from their states; ask so you can explain what will happen.
- **Which plug sensor a `cars:` car uses** (`charger_sensor`, a sensor that is `on` while the cable is in). It decides when the car is linked to an Easee. Ask the user which entity it is when the index has none or several; never take a charging-switch or a state text sensor for it. Tell the user that while two such cars are plugged in at home and not charging, neither is linked to the Easee (it is paused), and that without a plug sensor only the guest switch works on the Easee.
- **The link notification of a `cars:` car (or an Audi) on an Easee:** tell the user that every new link sends the guest notification to `notify_receiver` ("Charge now", and the kWh question when the car lacks `charge_limit` or `battery_sensor`). Ask whether the car has those two sensors: with both the app works out the kWh and does not ask. There is no option to turn the notification off.
- **Before enabling an Audi (`audi:`):** tell the user Audi support is work in progress and untested on a real car, and that the audiconnect README says `start_charger` / `stop_charger` could not be shown to work (a stop was confirmed and the car kept charging). So the hourly kWh cap and the fire alarm can not rely on stopping an Audi (the app does not fall back to lowering the charge target). Ask for the `device_id` and check every entity id against the index. After the first sessions, ask the user to compare the `Audi entities` and `linked. Plug sensor` INFO lines with what the car really did (the charging state strings above all). Checked against the complete audiconnect source (2.5.2): the plug binary_sensor is `on` when the plug state is not `invalid` or `disconnected` and `unavailable` when the car did not report it; entity ids are built as `<domain>.<vehicle title slug>_<entity name slug>` (the device_tracker is `device_tracker.<title>_position` and carries a `vin` attribute); `refresh_cloud_data` is registered and a charger action is followed by a cloud refresh about 5 s later; a car in `timer` charge mode charges only in its own timer windows (one INFO when `sensor.<car>_charging_mode` reads `timer`; the user switches it to manual in the myAudi app or with audiconnect's `set_charge_mode`). Still unverified: the exact charging-state strings the Audi API returns (third-party evidence only; the app logs an unknown one once) and whether start / stop take effect.
- **Before anything that acts alone:** charging control per car, heater and switch control, `automate`, `pause_charging`, `vacation_keep_off`, and the fire alarm turn-off (any app that fires `MODE_CHANGE` `fire` makes this app switch off heaters and stop cars).
- **Vacation:** which `input_boolean`, whether a heater or switch may be off on vacation (`vacation_keep_off`; for CC devices in the CC app).
- **The price app name** in the user's `apps.yaml` for `electricalPriceApp` and `dependencies`.
- **The hourly cap:** `max_kwh_goal` from the user's grid tariff step, never the default 15 unasked.

## Check an existing config (old or faulty keys)

Read the user's block before changing anything, and report:
- `climate:` entries: deprecated (WARNING), they still work. Suggest moving each to a ClimateCommander app listed under `climate_commander:`, one at a time and only with a yes. `climate:` manages only hvac mode `heat`; a climate used for `cool`, `fan_only`, `dry`, `auto` or `heat_cool` must be a CC app.
- Water heaters, dryers and other on/off switches stay in `heater_switches`. Never move them to ClimateCommander.
- A heater entry without `power:` (find the watts from the power sensor history, else ask).
- `windowsensors` on a `heater_switches` entry: ignored, WARNING. Remove it (window handling belongs to `climate:` or the CC app).
- `automate` that is not `true`/`false` or an entity id (WARNING, and automation is on).
- A heater `vacation` that is not an entity id string. A saved `false`/`null` is no longer repaired to the main switch: set `vacation:` on the entry if the heater should follow it.
- `turn_back_on_after`: 60 (default) is a power cycle that silences a beeping appliance, 0 keeps it off until someone turns it on. Ask which the user means.
- `dependencies:` between this app and ClimateCommander apps, either way: remove them (EM finds CC apps by name, and a dependency reloads EM on every CC reload).
- `electricalPriceApp` that does not name an existing app, or a `dependencies` name that is not an app.
- `vacation_keep_off` for a CC device belongs in the CC app (`vacation_keep_off: true`), not in this app.
- `away_state` at the top level (older name for `vacation`), `notify_reciever` (misspelled, ignored), `away` / `away_temp` (ignored since 1.0.2), `charge_on_solar` (the key is `charge_only_on_solar`), `daylight_savings` (the key is `daytime_savings`).
- A new `climate:` entry with `target_indoor_temp` but no `target_heater_temp` gets a heater target of 23: set `target_heater_temp` too.
- Times (`turn_off_after`, `daytime_savings` `start`/`stop`) must be quoted strings `'22:00:00'`.

## Behaviour not to "fix"

- Easee `disconnected` is reported as `awaiting_start` while a car is linked. Cars report wrong data when asleep, and the owner avoids waking them.
- A charge stopped in the Tesla app during planned charge time is started again within a minute.
- The 60 s start loop (also the wake-up for Teslas) has no `unavailable` filter on purpose: the repeated command wakes the car.
- `unlink_grace_minutes` (Tesla default 12) before a Tesla is unlinked from an external charger. Easee `ready_to_charge` counts as equal to a car reporting `Stopped` or `NoPower`, so no grace timer starts then; a new link still needs `awaiting_start` and `NoPower` (a `cars:` car or an Audi: its plug sensor `on`, used only when no car matches on `NoPower`).
- A car that is not home gets no start, stop, ampere, charge limit or wake-up command, also during a fire alarm: it may be fast charging somewhere else. A Tesla's or Audi's own charger is the car, so this covers it; the Easee can always be stopped. During a fire alarm a location that is `unavailable` or `unknown` counts as not home for the stop (WARNING).
- During a fire alarm each charger is checked on its own state, not the linked car's. The Easee is also paused in `ready_to_charge`, because that is how it starts charging.
- With two or more cars, a car is linked to the Easee (`awaiting_start`) only on data newer than the cable event: sleeping cars that are not charging are woken (`wake_when_stale: false` stops the WAKE_UP) and force-updated, checked every 30 s for up to 4 min, one round per 10 min. Two cars both reporting `NoPower` on fresh data means no link (Tesla API control through the car's own link still works). Do not link on cached data again: a sleeping Tesla's stale `NoPower` linked the wrong car for 4 hours. The same wait and the one-car rule apply to a `cars:` car linked on its plug sensor; two plugged-in `cars:` cars means no link, and a `NoPower` Tesla (also one still waiting for fresh data) goes before any plug match.
- A `cars:` car or an Audi linked to an Easee reads the Easee's state, so it is never unlinked for not reporting `NoPower` or `Stopped`. When the Easee has been `disconnected` for `guest_remove_after_minutes`, or another car was plugged in within that time, its plug sensor stands in for its own state: `on` = moved to its own charger (keeps its plan), `off` = unplugged.
- A `cars:` car or an Audi linked to an Easee gets the guest notification once per link (not for a Tesla, not on every check). Its buttons are `chargeNow<easee>|<vehicle_id>` and `kWhremaining<easee>|<vehicle_id>`, so a reply reaches only that car and only while it is still the car linked to that Easee; a late reply, or one after another car was linked, is ignored with an INFO line. The guest keeps `chargeNow<easee>` / `kWhremaining<easee>`. The kWh answer is a value for this charge (kept on the car while it stays linked to that Easee, forgotten at completion or a new link) and never changes `max_kWh_charged` or the battery size; it wins over the estimate, also over a `charge_limit` without `battery_sensor`. After a restart with the link saved, or with the car still in the charge queue, no notification is sent and the kWh is taken from the queue entry (as for a guest), even if the owner never answered.
- Climate setpoints are rounded half up to 0.5 (22.25 -> 22.5), then the vacation temperature and `min_temp` floors are applied and sent as they are (13.2 stays 13.2).
- Between 06:00 and 15:00 without tomorrow's prices (the price app gives no window then), a car that already has a window keeps it, a car that is charging and not finished is not stopped for the missing window, and a car plugged in during that time gets its window when the prices arrive. Between 09:00 and 14:00 a low price is charging time until then.
- A notification about the charge plan goes out only for a new or changed plan. A car that finishes, `waiting for prices` and `No planned charging` only update `infotext`.
- The 00:03 price refresh does not recalculate heater save/spend periods (they were found at about 13:00).
- `wake_when_stale: false` turns off the WAKE_UP for data older than 12 min; the data update press is still sent.
- Table mode: overconsumption and an open window use the LOWEST of the save and vacation temperature.
- A `temperatures` entry applies from its `out` value and up.
- Fire `false-alarm` turns on only what this app turned off. Heaters and switches stay off until `false-alarm`.
- A window must be open 120 s before the heater is lowered; closing acts at once. A window open at startup acts at once.
- Easee in `ready_to_charge`: 10 resume commands one minute apart, then one every 10 minutes with one WARNING and notification.
- An Easee reporting `charging` with no car linked is paused within a minute (one WARNING log line, no notification, at most 5 pause commands one minute apart). A car is not linked on `charging`, only in `awaiting_start` (a Tesla on `NoPower`, a `cars:` car or an Audi on its plug sensor), so a car already charging is paused first and then linked. A guest session counts as a linked car, and `unavailable` never pauses. Charging from outside the app with no linked car is not supported, and there is no option for it: the guest switch is the way to charge an unknown car.
- Heaters are never reduced before minute 3 of the hour. Priority 1 and 2 cars keep charging after the price rises.
- The stored cap rises by 5 kWh when the top 3 hours exceed it, and it (with the top hours) goes back to `max_kwh_goal` on the 1st of each month.
- `climate:` setpoints changed by hand are overwritten at the next run.
- Alert and monitoring apps may be meant to fire on `unavailable` / `unknown`: do not filter without asking.

## Events and API for other apps

- Listens to `MODE_CHANGE` (or the translated event name when Lightwand's `translations_lightmodes` is installed) on `main_namespace`: mode `fire` / `false-alarm` (or their translations). Sent by `fire_alarm` and `notify_mobiles` in Pythm_AppdaemonApps, and by ModeManagement.
- Listens to `WEATHER_CHANGE` (`temp`; `climate:` entries also use `rain` and `wind`).
- Listens to `mobile_app_notification_action` for its own notification buttons (find new charge time, add a high consumption hour, guest kWh / charge now, kWh / charge now for a car linked to an Easee on its plug, turn off a finished appliance).
- Sends to ClimateCommander devices: `set_power_state(state, valid_minutes = 45, source = 'ElectricalManagement')` with `normal`, `save`, `spend` or `reduce`, re-sent at least every 10 min and within a minute after a CC reload. Without a new call the CC device goes back to `normal` after 45 min. EM also reads `get_device(climate)`, `heater`, `namespace`, `vacation_entity`, `daytime_savings` and `in_daytime_saving()`.
- Fires no events. No app in the sibling repos calls this app with `get_app` (checked 2026-10-23).

## Dependency pitfalls

- `electricalPriceApp` must name an ElectricalPriceCalc app that exists and starts without errors. Otherwise this app logs an ERROR and stops. Put the same name in `dependencies` so the price app is loaded first.
- With `notify_app`, put that app in `dependencies` too: the app is looked up once at startup.
- A `dependencies` name that is not an app in `apps.yaml` stops the dependants from starting. Check every name.
- No `dependencies` from this app to ClimateCommander apps, or back. A `climate_commander` entry whose app is not running yet is retried every 30 s for 10 minutes.
- The Lightwand mode translation needs `dependencies: [mode_translation]` only if that app exists.

## Troubleshooting (log lines)

| Log line | Likely cause |
|---|---|
| `Configuration error in '<key>': ...` (ERROR) | Wrong type for a top-level key. The app does not start. |
| `electricalPriceApp '<name>' was not found ...` (ERROR) | Wrong name, the price app failed, or no `dependencies` on it. |
| `Set 'power' (the heater's rated watts) for: ...` | Entries without `power:`. Find the watts from the sensor history (`tools/ha_peak_power.py`), else ask the user. |
| `'climate:' heaters in ElectricalManagement are deprecated ...` | Old `climate:` list. Works; suggest CC apps. |
| `<climate> is in hvac mode '<mode>' ...` | `climate:` entry not in `heat`. Use a CC app. |
| `<switch>: windowsensors is ignored for switches.` | Remove `windowsensors` from the switch. |
| `<entity>: 'automate' must be a boolean or an entity id ...` | Wrong `automate` value. |
| `ClimateCommander app '<app>' was not found ...` / `... is not running (yet).` | Wrong app name, CC app failed, or an old list app without `climate:` in the entry. |
| `<entity> is configured twice in ElectricalManagement ...` | Same climate in `climate:` and `climate_commander:`. Keep one. |
| `Unknown option '<x>' in options. Ignored.` | Misspelled option. |
| `Electricity prices are not ready yet ...` | Price app has no prices yet. Normal right after a restart. |
| `Avg consumption during one hour is now ... New max kWh ...` | `max_kwh_goal` too low for the home, or `buffer` too small. |
| `<car>: no 'id' attribute on <online_sensor> ...` (ERROR) | Tesla integration not loaded, or a wrong `online_sensor`. Reload after it runs. |
| `Easee <charger> still ready_to_charge after ...` | Car asleep or stopped in the car app. |
| `Easee <charger> is charging with no car linked. Pausing it. ...` (WARNING, log only) | The car link ended while charging, a restart with no saved link, or a car the app does not know (use the guest switch). A `cars:` car or an Audi: its `charger_sensor` is missing (the line names those cars), not `on` yet (cloud delay, up to 15 min for an Audi; it is linked within a minute of turning `on`), the car is not home, it is charging on its own charger, or two cars match. |
| `low_price_max_continuous_hours needs ElectricalPriceCalc 0.1.8 or later ...` (WARNING, once) | ElectricalPriceCalc is older than 0.1.8. Tell the user to update it; until then spend hours use 3. |
| `<car>: '<key>: <value>' in the configuration is not valid ...` (ERROR) | `wake_when_stale` or `unlink_grace_minutes` has a wrong value; the default is used. |
| `<charger> reports charging during a fire alarm, but the car is not home. No stop sent.` (WARNING) | The car is charging somewhere else, or its location is `unavailable` / `unknown`. |
| `notify_app '<name>' was not found ...` (ERROR) | Wrong name, the notify app failed, or no `dependencies` on it. |
| `<charger>: no 'id' attribute on <sensor> ...` (ERROR) | Easee integration not loaded, or a wrong status sensor. Reload after it runs. |
| `<car>: Audi support is work in progress and untested on a real car ...` (WARNING, every start) | Expected for each `audi:` entry. |
| `<car>: no 'device_id' in the audi entry ...` (WARNING) | Add `device_id`; until then no start or stop goes to the Audi. |
| `<car>: Audi entities: plug ..., charging state ..., charge mode ... Now: plug '<raw>', charging state '<raw>' ...` (INFO, every start) | Expected for each `audi:` entry. Check the ids against the index and the raw strings against the car. `None` for an entity means it was not found: add the key. |
| `No VIN found for <car> (location_tracker: ...). Using '<car>' as vehicle id.` (INFO) | The `Position` device_tracker is missing (the car reports no position) or has no `vin` attribute yet. The car name is the id; without the tracker the car always counts as home. |
| `<car>: Audi stop is not confirmed to work with audiconnect (see its README); ...` (WARNING, once per start) | The first stop was sent to an Audi. Expected; the hourly cap and fire stop may not take effect for it. |
| `<car>: charging_state is 'conservation' ...` (INFO, once) | The Audi reached its target and tops the battery up itself. Treated as Complete. Nothing to do. |
| `<car>: charging_state is 'error' ...` (WARNING, once) | The car reports a charging error. Treated as Stopped; the app does nothing about the error. Check the car and the EVSE. |
| `<car>: charging_state is 'unsupported' ...` (WARNING, once) | The car does not report a charging state; the app keeps the last state. The Audi can still be linked to an Easee on its plug sensor. |
| `<car>: charge mode is 'timer' (<sensor>) ...` (INFO, once per switch into timer mode) | The car charges only in its own timer windows, so a start from the app may not take. Tell the user to set manual charging (myAudi app, or audiconnect `set_charge_mode`). |
| `No VIN found for device <device_id>` (ERROR in the Home Assistant log, from audiconnect) | The `device_id` in the audi entry is not an audiconnect device. Copy the device id from the device page URL in Home Assistant. |
| `<easee>: <car> linked. Plug sensor <id>: '<raw>', charging_state (<id>): '<raw>', <easee> status: '<raw>'.` (INFO) | A `cars:` car or an Audi was linked on its plug and the link notification was sent (or the session was restored after a restart). A plug that reads `on` while the car is not plugged in means the plug sensor is wrong for linking. |
| `Charge now / kWh remaining received for <vehicle_id> on <easee>, but that car is not linked to <easee>. Ignored.` (INFO) | A reply to the link notification after the car left the Easee or another car was linked. Nothing changed. |
| `kWh remaining received for <car>, but its own sensors give the kWh to charge. Ignored.` (INFO) | A kWh reply for a car with `charge_limit` and `battery_sensor`; the sensors decide. |
| `<car>: Unknown charging_state '<value>' ...` (WARNING, once per value) | The Audi reported a charging state the app does not know; it is ignored. Report the value. |
| `<car>: start charging was sent 3 times but the car still reports ...` (WARNING + notification) | Audi: the command did not take (audiconnect says start/stop are unproven), the car is in timer charge mode, or no new data yet (15 min polling). |
| `Skipping tesla / audi / easee entry ...` (WARNING) | The entry has no name and no sensor to take the name from. |
| `Could not automatically find <key>: ...` (INFO) | Entity not found by name. Add the key with the id from the index. |
| `Persistence file ... was moved to ... corrupt ...` (ERROR) | Unreadable JSON. The app starts with empty learned data. |

## Rules for the assistant

- One `apps.yaml` edit at a time. Back it up first, show the block, explain in one or two sentences what it will do, and get a yes.
- Entity ids only from the HA index. Never invent ids or app names. Watts come from the sensor history or the user, never from a guess.
- Do not edit the JSON file unless the user asks; stop AppDaemon's app first if they do (it is written on shutdown).
- No commit or push unless asked. No personal entity ids, names or addresses in the docs; use placeholders like `climate.living_room`.
