import { stat } from "node:fs/promises";
import { join } from "node:path";
import {
  createScoreOutbox,
  scoreConfigFromEnv,
  ScoreOutboxError,
} from "./evaluation_scores";

export async function replayEvaluationScores(
  args: string[] = process.argv.slice(2),
): Promise<number> {
  try {
    if (args.length !== 2 || args[0] !== "--output-dir" || !args[1])
      throw new ScoreOutboxError("INVALID_ARGUMENTS");
    try {
      await stat(join(args[1], "scores-outbox.jsonl"));
    } catch {
      throw new ScoreOutboxError("OUTBOX_MISSING");
    }
    const outbox = await createScoreOutbox(args[1], scoreConfigFromEnv());
    const report = await outbox.flush();
    console.log(JSON.stringify(report));
    return report.pending ? 1 : 0;
  } catch (error) {
    console.error(
      error instanceof ScoreOutboxError ? error.code : "REPLAY_FAILED",
    );
    return 1;
  }
}
if (/replay_evaluation_scores\.(?:ts|js)$/.test(process.argv[1] ?? "")) {
  void replayEvaluationScores().then((code) => {
    process.exitCode = code;
  });
}
