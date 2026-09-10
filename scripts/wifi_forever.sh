#!/usr/bin/env bash
# Make the Jetson reconnect to Wi-Fi forever, with no screen attached.
# Usage:  bash scripts/wifi_forever.sh "<SSID>" "<password>"
#         bash scripts/wifi_forever.sh            # reuse the network you are on now
# Effects: autoconnect on with unlimited retries, Wi-Fi power-save off, a systemd watchdog
# that re-runs "nmcli connection up" every 60 s while the gateway is unreachable.
set -euo pipefail

SSID="${1:-}"; PSK="${2:-}"
if [[ -z "$SSID" ]]; then
  SSID="$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2 ~ /wireless/ {print $1; exit}')"
  [[ -n "$SSID" ]] || { echo "Not on Wi-Fi now. Run: bash scripts/wifi_forever.sh \"<SSID>\" \"<password>\""; exit 2; }
  echo "Using the current Wi-Fi connection: $SSID"
else
  if ! nmcli -t -f NAME connection show | grep -qx "$SSID"; then
    sudo nmcli device wifi rescan >/dev/null 2>&1 || true
    sudo nmcli device wifi connect "$SSID" password "$PSK"
  fi
fi

echo "== autoconnect forever"
sudo nmcli connection modify "$SSID" connection.autoconnect yes \
  connection.autoconnect-priority 100 connection.autoconnect-retries 0 \
  ipv4.method auto ipv6.method auto 802-11-wireless.powersave 2
# 802-11-wireless.powersave 2 = disabled

echo "== disable Wi-Fi power saving at the driver level"
sudo mkdir -p /etc/NetworkManager/conf.d
printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/wifi-powersave-off.conf >/dev/null

echo "== watchdog: reconnect if the gateway stops answering"
sudo tee /usr/local/bin/bob-wifi-watchdog >/dev/null <<EOF
#!/usr/bin/env bash
# Runs every minute from systemd. If no default route or gateway ping fails, bring the connection up again.
GW=\$(ip route | awk '/default/ {print \$3; exit}')
if [[ -z "\$GW" ]] || ! ping -c 1 -W 3 "\$GW" >/dev/null 2>&1; then
  logger -t bob-wifi "link down, reconnecting to $SSID"
  nmcli radio wifi on
  nmcli connection up "$SSID" >/dev/null 2>&1 || { nmcli networking off; sleep 2; nmcli networking on; }
fi
EOF
sudo chmod +x /usr/local/bin/bob-wifi-watchdog

sudo tee /etc/systemd/system/bob-wifi-watchdog.service >/dev/null <<'EOF'
[Unit]
Description=Bob Wi-Fi watchdog
After=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/bob-wifi-watchdog
EOF
sudo tee /etc/systemd/system/bob-wifi-watchdog.timer >/dev/null <<'EOF'
[Unit]
Description=Run the Bob Wi-Fi watchdog every minute

[Timer]
OnBootSec=45s
OnUnitActiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now bob-wifi-watchdog.timer
sudo systemctl enable NetworkManager >/dev/null 2>&1 || true

echo
echo "Done. This Jetson will rejoin '$SSID' on every boot and retry every minute if it drops."
echo "IP now: $(hostname -I 2>/dev/null | awk '{print $1}')   (SSH to this from your Mac; no monitor needed)"
echo "Check later with:  systemctl list-timers | grep bob-wifi ; journalctl -t bob-wifi"
