# Large pull requests

Standard and Premium reviews plan bounded source chunks before starting the pull request overview. Files that are too large are split at hunks or line ranges with their original coordinates. Graph relationships from the reviewed revision keep related files together; path ordering is the fallback. Each chunk receives the pull request intent and a bounded map of the other changed files. A final integration reading checks interactions across chunks.

Before each model request, the complete prompt is checked against the context limit, including tools, response schemas and the output reserve. Tokenizer tables and binary model files receive artifact checks rather than line-by-line source review. Unverified provenance, missing loader references and unavailable integrity evidence remain coverage gaps. These gaps prevent approval.

## Allowances

Each planned source chunk contributes the standard **Model calls per review** allowance to the total, with additional calls reserved for overview and integration work. This is a shared allowance: evaluations and recovery consume the same total. Structural grouping does not start another model reading.

The **Model calls for large PRs** setting caps the whole review, including summaries, discovery, evaluations and truncation recovery. Its default is 256 calls. Operators set the deployment ceiling with `SOURCEANT_LARGE_REVIEW_MAXIMUM_CALLS`, which also defaults to 256. A workspace or repository allowance cannot exceed that ceiling.

The planned allowance grows with the chunk count until it reaches the configured ceiling. A child does not receive a fresh spending ceiling. A plan whose estimated minimum exceeds the allowance is rejected before paid discovery starts. The counter is shared across workers and continuation jobs. A new worker window does not reset it.

## Concurrency

**Parallel review participants** limits simultaneous model requests across the entire review, including nested summary batches, discovery, evaluation and retries. The deployment-wide model limit also applies. Independent chunks run in parallel within these limits; smaller chunks do not multiply simultaneous model requests.

## Continuation and cancellation

Background PR reviews persist their plan, review chunks and provider responses. Each chunk records its review, explanatory group, exact files and hunk ranges, status, claim attempts and result. Groups do not affect scheduling. Plan and result documents use JSON storage. Background reviews continue automatically in another worker window when needed. Saved responses are reused without recording usage twice. Review-run progress includes chunk states and the shared call count. Cancelling a run blocks new model calls, cached-response replay and late chunk completion.

If a provider finishes just before a process fails and its response has not been saved, that request may need to run again. A provider request and a database write cannot form one atomic transaction.

Apply database migrations before starting workers. All workers continuing a review must use the same database. Changed settings or intent invalidate the persisted plan rather than granting it a fresh allowance.
