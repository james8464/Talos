import { describe, expect, it } from "vitest";

import {
  DEFAULT_INBOX_PROMPT_TEMPLATE,
  renderInboxNotificationPrompt,
} from "../../../src/agent/prompt/index.js";

describe("Inbox notification prompts", () => {
  it("keeps routing policy in role instructions and the notification payload compact", () => {
    expect(DEFAULT_INBOX_PROMPT_TEMPLATE).toContain("{{inbox_updates}}");
    expect(DEFAULT_INBOX_PROMPT_TEMPLATE).toContain(
      "Use the messages shown above when they provide enough context",
    );
    expect(DEFAULT_INBOX_PROMPT_TEMPLATE).toContain(
      "not already represented in your current context",
    );
    expect(DEFAULT_INBOX_PROMPT_TEMPLATE).toContain(
      "Read earlier messages only when they may affect your response",
    );
    expect(DEFAULT_INBOX_PROMPT_TEMPLATE).not.toContain(
      "Read the full conversation",
    );
    expect(DEFAULT_INBOX_PROMPT_TEMPLATE).not.toContain(
      "Respond to direct mentions",
    );
    expect(DEFAULT_INBOX_PROMPT_TEMPLATE).not.toContain(
      "teamhub message direct",
    );
  });

  it("renders typed template variables", () => {
    expect(
      renderInboxNotificationPrompt(
        "{{agent_name}}|{{changed_inbox_count}}|{{changed_inboxes}}|{{triggered_at}}",
        {
          agent_name: "Reviewer",
          changed_inbox_count: "2",
          changed_inboxes: "inbox-a\ninbox-b",
          triggered_at: "2026-08-17T00:00:00.000Z",
        },
      ),
    ).toBe("Reviewer|2|inbox-a\ninbox-b|2026-08-17T00:00:00.000Z");
  });
});

describe("Room reply contract", () => {
  it("ends both Meta prompts with the exact send command and example", async () => {
    const { renderMetaInstructions } =
      await import("../../../src/agent/prompt/meta-instructions.js");
    const { renderCompactMetaInstructions } =
      await import("../../../src/agent/prompt/compact-meta-instructions.js");
    const input = {
      agentName: "Talos",
      agentActorId: "actor_talos",
      agentType: "anotherme" as const,
      representedHuman: {
        displayName: "Owner",
        actorId: "actor_owner",
      },
      roomId: "room_1",
      roomKind: "direct" as const,
      connectedApps: false,
      connectedAppKeys: [],
      connectableAppKeys: [],
    };
    for (const render of [
      renderMetaInstructions,
      renderCompactMetaInstructions,
    ]) {
      const prompt = render(input);
      expect(
        prompt.endsWith("Do not repeat the reply as assistant text."),
      ).toBe(true);
      expect(prompt).toContain(
        'teamhub message send --content "Hello! I\'m Talos, your AI assistant here to help with onboarding and tasks."',
      );
    }
  });
});
