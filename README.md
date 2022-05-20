# Installation

To install via pip, run:

```bash
pip install gitparent
```

To install from the repo, run:

```bash
git clone https://github.com/gitparent/gitparent.git
cd gitparent
pip install .
```

After installation, you can run the utility by using the `gitp` command. 

Python 3.9+ and [pyyaml](https://github.com/yaml/pyyaml) are required.

# About

**gitparent** is largely based off of [meta](https://github.com/mateodelnorte/meta) and [gitman](https://github.com/jacebrowning/gitman), both of which are lightweight layers on top of git which facilitate and manage projects consisting of nested git repos. Rather than adding complexity at the git level (e.g. git subtree, git submodule) or adding a heavyweight tooling layer with its own paradigms (e.g. [git-repo](https://gerrit.googlesource.com/git-repo/)), gitparent opts for the meta/gitman approach: provide a thin multi-repo management layer and let git shine.

Why gitparent rather than meta or gitman? It boils down to preference, but here are some of the key differentiators:

- Simple hierarchical status querying via `gitp status` (absent in both meta and gitman)
- Simple manifest format to minimize git conflict resolution (lacking in gitman)
- General purpose utility operations for child repos (lacking in gitman)
- Rev control for child repos (lacking in meta)
- Built-in external [linking mechanic](#Linking) (absent/lacking in meta and gitman)
- [Link "overlaying"](#Linking) to override shared repo dependencies (absent/lacking in meta and gitman)
- Favors Python projects due to being written in Python (i.e. one less dependency; meta is written in nodeJS)

# Purpose

1. Support all work modes described in the table in the [Philosophy](#Philosophy) section.
2. Help user manage changeset distribution.
3. Track changes to determine when particular changesets (possible across multiple repos) are made.


# Philosophy

The following table represents the progression of multi-repo/multi-dependency projects in order of project maturity and describes the optimal form the dependencies take at each stage in a development environment.

| Stage | Type of repo/project         | Ideal Source of Depencencies                                                                                                              |
|-------|------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| 1     | Immature/unstable projects   | unversioned dependency repos cloned onsite (git multirepo)                                                                                |
| 2     | Semi-mature/stable projects  | package managers (local modifications only or read-only) -or- versioned (tag, branch) clones of dependency repos onsite (git multirepo)   |
| 3a    | Mature/stable projects       | package managers (local modifications only or read-only)                                                                                  |
| 3b    | Large, packaged IP           | (sym)links, fileshares (read-only, no local copies made)                                                                                  |

In stage 1, we have an intention to break our project into multiple repositories, but the speed at which changes are being made is so great that maintaining versions for each dependency across repos doesn't justify the cost. Stage 1 lends itself to a sort of "virtual monorepo" work flow wherein the project is technically composed of multiple repositories but functions as a singular repository. As soon as the project reaches a stage wherein continuous integration becomes sufficiently complex and the number of collaborators and/or level of autonomy of each individual child repository increases, the project would be best served by moving to either stage 2 or 3.

In stage 2, the project is somewhat mature and each child repository has some level of autonomy (folks are contributing to and operating at the child repo level rather than always at the top level, development of individual child repos is driven by different timelines/external factors). Versioned git repositories may be favorable in the case wherein occasional local development across multiple repos is required. For repo relationships that do not have this requirement or require some pre-generated collateral to be present at the time of consumption (e.g. any generation process that cannot/should not be reproduced by consumers of a dependency), package managers may be a better fit to allow for local copies of pre-packaged dependencies to be downloaded in an ephemeral store locally in the developer's workspace/environment.

In stage 3, the project has reached a level of maturity that warrants a strict release and integration process between all dependent repos in the project. This can either be achieved via the aforementioned package manager model (3a, downloading a local copy of pre-packaged dependency content), or for dependencies that take up significant disk space, via logical links to a static path within a shared compute environment (3b).

gitparent attempts to provide a full solution to 1, 2, and 3b in the table above, and seeks to enable integration of package managers for 2/3a.


# Linking

gitparent provides ways to describe child repos as links to support the following usecases:

1. A common dependency exists across multiple child repos which should all be the same version. Linking them all to one source lets developers make changes to that common dependency in one place for the whole project. Link overlaying would be used in this case.
2. In a shared compute environment, dependencies can be linked to static read-only paths. This is helpful if a project contains dependencies that are very large or are installed statically in a compute environment. Normal links or link overlaying may apply in this case.

The difference between a normal link and an overlay link is that normal links are stored as state at the parent level of the target of that link whereas overlays are stored as state only at the top level repo. Link overlays are ignored if the repo in question is not the top-most repo. Take the following example:

```
repo A
    |
    |- repo child_of_A
        |- repo grandchild_of_A
       
```

If we were to create an ordinary link for `grandchild_of_A` to some static path in our system from repo `A`, that link information would be stored within the manifest of `child_of_A` (the parent of `grandchild_of_A`). This means that if we commit that change and then cloned `child_of_A` independently, we would see `grandchild_of_A` as the link we created.

If we were to create an overlay link for `grandchild_of_A` to some static path in our system (or to some other child repo that falls under repo `A`), that link information would be stored within the manifest of `A`. If we were to commit that change and clone a fresh copy of `child_of_A`, we would not see a link created for `grandchild_of_A`. We would only see that link created if we cloned `A`. Furthermore, if `A` itself is a child repo to some other, higher-order repo, that repo doesn't apply a link overlay to `grandchild_of_A`, and we cloned the higher-order repo, we again would not see a link for `grandchild_of_A` since overlays are only evalutated at the level in which they were created (i.e. `A`).

# Schema

The format of the `.gitp_manifest` file which stores gitparent state information is as follows:

```yaml
repos:
    <path to instance of child repo>:
        url: <repo URL>
        branch: <branch or tag to track>
        commit: <commit SHA to track -- takes precedence over branch if both are specified>
        type: <repo|overlay>
    <another path to a different child repo>:
        ...
post_clone:
    - <first system command to execute upon doing a `gitp clone` on this repo>
    - <second system command "">
        ...
post_pull:
    - <first system command to execute upon doing a `gitp pull` on this repo>
    - <second system command "">
        ...
```

The commands listed under `post_clone` and `post_sync` are run in the order specified and in the root repo directory. As the names suggest, `post_clone` is triggered after a `gitp clone`, after the associated repo has been cloned (but not its children). Similarly, `post_pull` is triggered after all children of a given repo have been pulled via `gitp pull` (but before overlays are applied).

The `GITP_PARENT_REPO` environment variable is set during `gitp pull` and `gitp clone` operations to communicate to any processes invoked via `post_clone` or `post_sync` whether or not the current repo is being consumed as a parent repo or as a child repo. This is useful if you wish to execute certain commands/run certain processes contingent on how the repo is being consumed.
