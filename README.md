<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/sourceant-lockup-colour-dark.svg">
    <img src="docs/assets/sourceant-lockup-colour.svg" alt="SourceAnt" width="600">
  </picture>
</p>

<p align="center"><strong>Software intelligence for you and your coding agents.</strong></p>

AI is writing code faster than you and your team can understand it. SourceAnt helps you stay ahead. It shows you what is being built, checks changes against your decisions and requirements, and keeps your system architecture visible as the code moves.

Let agents move fast without losing control of where the software goes.

**[Run it](#run-it-locally)** on your computer now. No account required.

<p align="center">
  <img src="docs/assets/sourceant-demo.webp" alt="The code graph for a repository, then a review of a working tree with its verdict, what it found, and the commits behind it" width="900">
</p>

## Use cases

Out of the box, on your own machine:

- **Code graph.** Every repository you add, parsed into files, symbols and the relationships between them.
- **Local code review.** Read a checkout through the same reviewer a pull request goes through.
- **Review on pull requests.** The same reviewer on the forge, commenting where the line is.
- **Knowledge and requirements.** What you decided and what the software must do, kept between sessions and linked to the code that carries it.
- **Skills over MCP.** Choose and apply your skills from coding tools that support MCP prompts.
- **One place for every index.** All your repositories in a single local store, each under its own scope, and nothing written into your folders.
- **Review across repositories.** A change in one repository read against the others it reaches.

## Run it locally

Install the CLI.

~~~bash
curl -fsSL https://raw.githubusercontent.com/sourceant/cli/main/scripts/install.sh | sh
~~~

~~~bash
sourceant setup
~~~

Start SourceAnt and open it in your browser.

~~~bash
sourceant ui
~~~

Add a repository from the Repositories page and it is parsed and kept current.
Point an MCP client at `http://127.0.0.1:8930/mcp`, or copy the block Settings
gives you.

Docs: [sourceant.ai/docs](https://sourceant.ai/docs).

## How it works

SourceAnt parses your repositories into one graph and serves it over MCP, so
your coding tools read what its reviews read. Knowledge you record sits in
that graph beside the code it governs, so a review of one file finds the
decision that constrains it.

| | |
|---|---|
| Code | `search_code`, `trace_code` |
| Local indexing | `get_index_status`, `index_repository` |
| Skills | `search_skills`, `get_skill`, `save_skill`, `delete_skill` |
| Knowledge | `put_knowledge`, `search_knowledge`, `put_knowledge_relationship` |
| Requirements | `put_requirement`, `link_requirement`, `search_requirements`, `get_requirement_coverage` |
| System | `put_topology_entity`, `put_topology_relationship`, `traverse_topology` |
| All of it, in one pack | `get_context` |

You ask for it in plain language:

> Remember that project shop uses signed webhook requests. Store it as an approved decision.

> Connect the signed webhook decision to the rule that rejects unsigned requests.

> Get the approved knowledge related to the signed webhook decision before changing its handler.

## Reviews across repositories

A change can break a repository it never mentions. Rename a route, and the client that calls it breaks under the old name. That name is nowhere in the diff.

SourceAnt records how your repositories are joined, and reviews across that join. A review searches each repository the change reaches for what would break there. A repository it could not read is named rather than skipped.

The joins come from two places. A manifest declares a dependency. A URL one service calls is declared nowhere, so a model reads the code for those and proposes them, quoting the lines. You approve a proposal before it counts.

## SourceAnt Cloud

The core is MIT licensed and self-hostable in full. [SourceAnt Cloud](https://app.sourceant.ai) runs the same engine with a managed layer on top: memory your team curates, contract analysis, continuous indexing at scale, the explorable graph, workspaces and roles, and analytics.

---

| License | Contact | Maintainer | Contributing |
|---|---|---|---|
| [MIT](LICENSE.md), copyright Whilesmart LLC | hello@sourceant.ai | [WhileSmart](https://whilesmart.com) | [CONTRIBUTING](CONTRIBUTING.md) |

<a href="https://github.com/sourceant/sourceant/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=sourceant/sourceant" />
</a>
