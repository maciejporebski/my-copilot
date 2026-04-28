import { joinSession } from "@github/copilot-sdk/extension";

const FAST_MODEL = "gpt-5.4-mini";
const TIMEOUT_MS = 120_000;

let session;

function buildFastPrompt(request) {
    return `Handle this as a fast, lightweight, single-message request.

Work directly and concisely. You may use tools and edit code when the request calls for it. Do not perform destructive operations, handle secrets, or attempt production changes unless the user explicitly asked for that and the normal permission flow allows it. If the request is unsafe, ambiguous, or too complex for fast mode, say so plainly and explain what workflow is needed instead.

Request:
${request}`;
}

session = await joinSession({
    commands: [
        {
            name: "fast",
            description: `Run one easy request on ${FAST_MODEL}, then restore the foreground model.`,
            handler: async (context) => {
                const request = context.args.trim();

                if (!request) {
                    await session.log("Usage: /fast <easy request to delegate>", { level: "warning" });
                    return;
                }

                const { modelId: originalModel } = await session.rpc.model.getCurrent();
                await session.log(`Running /fast on ${FAST_MODEL}...`, { ephemeral: true });

                try {
                    if (originalModel !== FAST_MODEL) {
                        await session.rpc.model.switchTo({ modelId: FAST_MODEL });
                    }

                    await session.sendAndWait(
                        {
                            prompt: buildFastPrompt(request),
                            mode: "enqueue",
                        },
                        TIMEOUT_MS,
                    );
                } catch (error) {
                    await session.log(`Fast mode failed: ${error.message}`, { level: "error" });
                } finally {
                    if (originalModel && originalModel !== FAST_MODEL) {
                        try {
                            await session.rpc.model.switchTo({ modelId: originalModel });
                        } catch (restoreError) {
                            await session.log(
                                `Fast mode could not restore model ${originalModel}: ${restoreError.message}`,
                                { level: "error" },
                            );
                        }
                    }
                }
            },
        },
    ],
});
