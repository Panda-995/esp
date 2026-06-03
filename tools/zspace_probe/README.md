# ZSpace Web API Probe (LOCAL ONLY)

This folder contains a self-contained Python script that logs into your
ZSpace management web on `https://192-168-101-12.z423-8ou2.direct.zspace.link:5056/`
and dumps the JSON responses of the most relevant monitoring endpoints.

**The script never sends your credentials to anyone except the ZSpace NAS itself.**

## Run

```powershell
cd 'c:\Users\Administrator\Documents\New project 2'
pip install requests
python tools\zspace_probe\fetch_zspace.py
```

You will be prompted for username and password. The script writes 5 JSON
files to `tools\zspace_probe\zspace_dumps\`:

| File | Endpoint | Purpose |
|---|---|---|
| `01_login.json` | `POST /auth/login` | Raw login response (contains token) |
| `02_polling2.json` | `POST /system/polling2` | Main status poll — likely contains temperature/fan fields |
| `03_system_home.json` | `POST /system/home` | Home overview |
| `04_loginlist.json` | `POST /device/loginlist` | Currently logged-in devices |
| `05_fan_set.json` | `POST /fan/set` | Fan mode (query-like) |

## Sharing with your assistant

Open each JSON, replace sensitive values with `<REDACTED>`, then paste
the redacted text into chat. Sensitive keys are:

- `token` / `rtoken` / `atoken` / `btoken`
- `password` / `pwd` / `passwd`
- `device_id` / `rdeviceid` / `rdevice` / `ruser`
- `secret` / `session` / `cookie`

Field names and numeric values are safe to share.

## Why local only

- Your credentials never enter the assistant's context.
- All traffic stays between your PC and the NAS.
- You control exactly what is shared (and redacted) afterwards.
