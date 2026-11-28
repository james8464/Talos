# Talos Browser Bridge

Chrome/Chromium extension that sends page context to Talos through a native
messaging host.

## Install

1. Build the native host:

   ```sh
   make build
   ```

2. Register the native host and extension ID:

   ```sh
   ../../scripts/install-native-host.sh
   ```

3. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**,
   and select this folder.

4. Enable **Settings → App Server** in Talos.

The popup can ping the host, save the current page context, ask Talos about the
page or selection, poll for the reply, and cancel the run.
