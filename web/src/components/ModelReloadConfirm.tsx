import { ConfirmDialog } from "@/components/ConfirmDialog";
import { HERMES_BASE_PATH } from "@/lib/api";
import { freshChatUrl } from "@/lib/fresh-chat";

/**
 * Confirm a fresh chat after a model change.
 *
 * Changing the main model persists to config.yaml, but the RUNNING chat keeps
 * its model until its session is rebuilt. Ordinary reloads reattach the tab's
 * keep-alive PTY, so the chat page explicitly requests a fresh terminal. Other
 * pages navigate to Chat with the same one-shot fresh intent. We confirm
 * first because this starts a fresh chat (the current one stays resumable
 * in Sessions and the agent's memory is kept).
 *
 * Shared by the chat sidebar picker and the Models page so both behave
 * identically. `model` is the short model name awaiting confirmation, or null
 * when the dialog is closed.
 */
export function ModelReloadConfirm({
  model,
  description,
  onCancel,
  onConfirm,
}: {
  model: string | null;
  /** Override the default body copy (e.g. the Models-page phrasing). */
  description?: string;
  onCancel: () => void;
  /** The mounted chat can start a fresh PTY without reloading the dashboard. */
  onConfirm?: () => void;
}) {
  return (
    <ConfirmDialog
      open={model !== null}
      title="Switch model?"
      description={
        description ??
        `Switching to ${model ?? ""} starts a fresh chat. Your current chat stays in your Sessions list and the agent's memory is kept. Start a new chat now to apply it?`
      }
      confirmLabel="Start new chat"
      onConfirm={onConfirm ?? (() => window.location.replace(freshChatUrl(window.location.href, HERMES_BASE_PATH)))}
      onCancel={onCancel}
    />
  );
}
