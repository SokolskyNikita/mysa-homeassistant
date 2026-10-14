# Manage schedules and holds

[Documentation index](../README.md)

## Schedules and holds

These actions require a Mysa sign-in, cloud access and a thermostat on that account. Creating or clearing a schedule also requires the thermostat to belong to a Mysa home. The actions target Mysa Local climate entities. Once a Mysa schedule is saved, Home Assistant does not need to stay running for it to run.

| Action | Effect |
|---|---|
| `mysa_local.hold` | Holds a temperature and optional mode over an existing Mysa schedule. Omit `until` for a permanent hold, or supply a future date and time. The state is applied immediately. |
| `mysa_local.resume_schedule` | Ends the hold and resumes the Mysa schedule |
| `mysa_local.set_schedule` | Replaces the thermostat's weekly Mysa schedule |
| `mysa_local.clear_schedule` | Removes the thermostat's Mysa schedule |

Schedule replacement saves a private backup before changing the cloud schedule. If a request fails, the integration checks what Mysa actually applied and restores the original when necessary. Unfinished recovery survives an HA restart and retries after cloud updates. Recovery preserves shared schedules and avoids overwriting unrelated changes made in the Mysa app.

For a weekday schedule:

```yaml
action: mysa_local.set_schedule
target:
  entity_id: climate.living_room
data:
  events:
    - days: [mon, tue, wed, thu, fri]
      time: "06:30"
      temperature: 21
    - days: [mon, tue, wed, thu, fri]
      time: "22:00"
      temperature: 18
```

Days are `mon` through `sun`. Each event accepts `hvac_mode: heat`, `cool` or `off`; the default is `heat`. Temperatures in these custom actions are Celsius. Schedule times use the thermostat's time zone, so check that it matches Home Assistant's.

To hold 22 °C until you resume the schedule:

```yaml
action: mysa_local.hold
target:
  entity_id: climate.living_room
data:
  temperature: 22
  hvac_mode: heat
```

A hold needs an existing Mysa schedule. For schedules managed entirely by Home Assistant, use automations that call the climate entity's normal actions. Those use local control while the thermostat is paired and reachable.
