// Discord Activity login. Inside Discord's iframe the redirect login can't run
// (Discord won't show its login page in a frame), so the Embedded App SDK asks
// the Discord client for an OAuth code instead. The backend exchanges it like
// any other code, with flow "activity" (no redirect URI).

// Discord launches an Activity with these query params; the SDK needs them.
export const inActivity = new URLSearchParams(location.search).has("frame_id");

const READY_TIMEOUT_MS = 15000;

let sdkReady = null;

function connect(clientId) {
  sdkReady ??= import("./vendor/discord-sdk.js").then(async ({ DiscordSDK }) => {
    const sdk = new DiscordSDK(clientId);
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error("Discord didn't answer. Close the Activity and launch it again.")),
        READY_TIMEOUT_MS,
      );
    });
    try {
      await Promise.race([sdk.ready(), timeout]);
    } finally {
      clearTimeout(timer);
    }
    return sdk;
  });
  // A failed handshake shouldn't stick: let "Try again" reconnect.
  sdkReady.catch(() => { sdkReady = null; });
  return sdkReady;
}

/** Ask Discord for a one-time OAuth code for this user. The first time, Discord
 *  shows its own consent dialog; after that it answers silently. */
export async function activityCode(config) {
  const sdk = await connect(config.client_id);
  try {
    const { code } = await sdk.commands.authorize({
      client_id: config.client_id,
      response_type: "code",
      state: "",
      prompt: "none",
      scope: config.scopes,
    });
    return code;
  } catch {
    throw new Error("Discord login was cancelled.");
  }
}
