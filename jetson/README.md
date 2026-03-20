# Jetson Service Templates

This folder contains example systemd unit files for a public, DIY-friendly setup.

Before installing them, replace these placeholders:

- `YOUR_USERNAME`
- `YOUR_MODEL_FILENAME.gguf`
- `YOUR_UM34C_BLUETOOTH_MAC`

## Files

- `llama-cpp.service.example`: runs `llama-server` on the Jetson
- `crankgpt.service.example`: runs the Flask kiosk app
- `um34c-rfcomm.service.example`: binds the UM34C to `/dev/rfcomm0`

## Install Flow

1. Edit the `.example` files and replace the placeholders.
2. Copy them into `/etc/systemd/system/`.
3. Reload systemd.
4. Enable and start the services.
5. Check service status and logs.

Example commands:

```bash
sudo cp jetson/llama-cpp.service.example /etc/systemd/system/llama-cpp.service
sudo cp jetson/crankgpt.service.example /etc/systemd/system/crankgpt.service
sudo cp jetson/um34c-rfcomm.service.example /etc/systemd/system/um34c-rfcomm.service
sudo systemctl daemon-reload
sudo systemctl enable --now llama-cpp.service crankgpt.service um34c-rfcomm.service
sudo systemctl status llama-cpp.service crankgpt.service um34c-rfcomm.service
```

## Finding The UM34C Bluetooth MAC

You can discover it from the Jetson with `bluetoothctl`:

```bash
bluetoothctl scan on
bluetoothctl devices
```

Use the address reported for your UM34C in `um34c-rfcomm.service.example`.

Typical pairing flow:

```bash
bluetoothctl pair YOUR_UM34C_BLUETOOTH_MAC
bluetoothctl trust YOUR_UM34C_BLUETOOTH_MAC
bluetoothctl connect YOUR_UM34C_BLUETOOTH_MAC
```

If your app service cannot open `/dev/rfcomm0`, add that user to the `dialout` group and restart the service.

## Notes

- `crankgpt.service.example` does not require the UM34C service to succeed before the app starts, because the app still supports a manual unlock fallback.
- `llama-cpp.service.example` reflects the currently tested Jetson settings for this project.
