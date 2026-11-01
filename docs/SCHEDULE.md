# Scheduling (templates in contrib/)
* Test run: `contrib/psmphealth.service` + `.timer` (default every 30 min; one run at a time thanks to run.lock; use `--parallel` for long lists).
* Heartbeat: `contrib/psmphealth-heartbeat.service` + `.timer` (hourly).
Install (needs root; edit User=/paths first): `sudo cp contrib/psmphealth*.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now psmphealth.timer psmphealth-heartbeat.timer`
Pause everything: `python3 run-psmp-tests.py --env prod --pause`.
