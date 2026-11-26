import { defineMessages } from "@/i18n/messages";

export const settingsMessages = defineMessages("settings", {
  navigation: "Settings navigation",
  title: "Settings",
  sections: "Settings sections",
  profile: "Profile",
  security: "Security",
  computers: "Computers",
  models: "Models",
  language: "Language",
  loadingComputers: "Loading Computers",
  loadingContent: "Loading content",
  languageDescription: "Choose the language Talos is shown in.",
  interfaceLanguage: "Interface language",
  interfaceLanguageHint:
    "Saved on this device. Text written by Agents and other people stays as they wrote it.",
  languageUnavailable:
    "That language could not be loaded. Check your connection and try again.",
  screenHighlight: "Test screen highlight",
  screenHighlightLabel: "Talos highlight preview",
  screenHighlightHint:
    "Outlines a sample area without speaking or taking focus.",
  mediaReadiness: "Camera and microphone",
  cameraPermission: "Camera",
  microphonePermission: "Microphone",
  permissionGranted: "Granted",
  permissionNotGranted: "Not granted",
  permissionUnknown: "Unknown",
  allowPermission: "Allow",
  captureCheck: "Test camera and microphone",
  captureCheckDone:
    "Captured a {frame}-byte frame and {audio}-byte clip; media deleted.",
  captureCheckFailed:
    "Capture failed. Check the macOS camera and microphone permissions.",
  saveDiagnostics: "Save diagnostics…",
  saveDiagnosticsFailed: "Diagnostics could not be saved. Try again.",
});
