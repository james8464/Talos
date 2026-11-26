/**
 * The last instruction a Meta prompt carries. Small local models sometimes
 * finish with assistant text after reading the earlier rules, so the exact
 * command and a worked example sit at the end where recency helps most.
 */
export const REPLY_CONTRACT = `## Room reply contract
The turn is not finished until you have run the message command. Your assistant text is invisible to people; only shell commands send.
- Run this as its own shell call, then finish: teamhub message send --content "<your reply>"
- Example: to answer "Hello Talos. Introduce yourself.", run
  teamhub message send --content "Hello! I'm Talos, your AI assistant here to help with onboarding and tasks."
- Then end the turn. Do not repeat the reply as assistant text.`;
