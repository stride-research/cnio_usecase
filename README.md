# cnio_usecase

CNIO's CageFlow pipeline, wrapped to run on HPC infrastructure via
[flowgentic](https://github.com/stride-research/flowgentic). This repo holds the
domain-specific glue, stage implementations, and configs; `flowgentic` holds the
reusable execution/metrics/orchestration layer.

## CI

CI runs the pipeline end to end as a GPU job on the cluster, through a
self-hosted runner. Because that job runs under a maintainer's cluster account,
CI only runs code that someone with write access has put in this repository.

- A pull request from a branch of this repository runs CI on every push. A new
  push cancels the run in progress and its cluster job.
- A pull request from a fork never runs on the cluster. The job is skipped, and
  maintainers do not approve workflow runs for fork pull requests.
- A maintainer can run CI on any branch, tag, or commit of this repository from
  the Actions tab: open "CageFlow CI", choose "Run workflow", and enter the ref.
  The same works from the command line with
  `gh workflow run ci.yml -f ref=<branch>`.
- To test a fork pull request, a maintainer reviews it, pushes the reviewed
  commits to a branch in this repository, and runs CI on that branch.

Contribution guide — TODO, coming soon.
