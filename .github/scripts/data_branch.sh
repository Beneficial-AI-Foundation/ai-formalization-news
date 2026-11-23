#!/usr/bin/env bash
# Keep the generated data on its own branch, so the hourly job never commits to main
# (which needs a reviewed pull request) and main's history holds only code.
#
#   data_branch.sh checkout BRANCH DIR   check out origin's BRANCH into DIR as a git
#                                        worktree; if origin has no BRANCH yet, start
#                                        it as an empty branch with no history
#   data_branch.sh commit BRANCH DIR     commit the changes in DIR and push them to
#                                        origin's BRANCH, unless only meta.json changed
#
# Run it from the main checkout. Fetches and pushes run there, where actions/checkout
# stored the credentials; the worktree is used only for local commits and rebases.
set -euo pipefail

usage() { echo "usage: $0 checkout|commit BRANCH DIR" >&2; exit 2; }
[ "$#" -eq 3 ] || usage
cmd=$1 branch=$2 dir=$3
remote_ref="refs/remotes/origin/${branch}"
retry_seconds=${DATA_BRANCH_RETRY_SECONDS:-5}

fetch() { git fetch --quiet --no-tags origin "+refs/heads/${branch}:${remote_ref}"; }

start_branch() {
  git worktree add --quiet --detach "$dir"
  git -C "$dir" switch --quiet --orphan "$branch"
  printf 'http_cache.json\n' > "$dir/.gitignore"
  cat > "$dir/README.md" <<EOF
# Generated data

The "Update news" workflow on \`main\` writes this branch every hour: \`news.json\`,
\`search.json\`, \`meta.json\`, \`seen.json\`, \`feed.xml\` and \`archive/YYYY-MM.json\`.
The code and its README are on \`main\`.

To take an item off the page, delete it from its \`archive/YYYY-MM.json\` file on this
branch. The next run rebuilds the other files from the archive, and \`seen.json\` keeps
the item from coming back.
EOF
}

case "$cmd" in
  checkout)
    rc=0
    git ls-remote --exit-code --heads origin "refs/heads/${branch}" > /dev/null || rc=$?
    if [ "$rc" -eq 0 ]; then
      fetch
      git worktree add --quiet -B "$branch" "$dir" "$remote_ref"
      echo "Checked out ${branch} at $(git -C "$dir" rev-parse --short HEAD) into ${dir}."
    elif [ "$rc" -eq 2 ]; then  # ls-remote found no such branch
      start_branch
      echo "origin has no ${branch} branch yet; this run starts it."
    else
      echo "::error::Could not list the branches of origin (git ls-remote exit code ${rc})."
      exit 1
    fi
    ;;

  commit)
    # meta.json (the run time) is committed only together with a real change, so an
    # hour with nothing new makes no commit. http_cache.json never is.
    if [ -z "$(git -C "$dir" status --porcelain -- . ':(exclude)meta.json' ':(exclude)http_cache.json')" ]; then
      echo "No new data; nothing to commit."
      exit 0
    fi
    export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-github-actions[bot]}"
    export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"
    export GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$GIT_AUTHOR_NAME}"
    export GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$GIT_AUTHOR_EMAIL}"
    git -C "$dir" add -A -- .
    # .gitignore already keeps it out; this also holds if that file is ever lost.
    git -C "$dir" rm --cached --quiet --ignore-unmatch -- http_cache.json
    git -C "$dir" commit --quiet -m "Update news data"
    # If someone changed the branch during the run (an edit by hand), replay this
    # commit on top of it, so the edit is never lost. If the replay conflicts or the
    # push keeps failing, stop with an error: the workflow then does not save the HTTP
    # cache, so the next run fetches these items again.
    for attempt in 1 2 3; do
      if git push --quiet origin "refs/heads/${branch}:refs/heads/${branch}"; then
        echo "Pushed $(git -C "$dir" rev-parse --short HEAD) to ${branch}."
        exit 0
      fi
      [ "$attempt" -lt 3 ] || break
      sleep $((attempt * retry_seconds))
      fetch
      if ! git -C "$dir" rebase --quiet "$remote_ref"; then
        git -C "$dir" rebase --abort || true
        echo "::error::Could not replay the data commit on top of origin/${branch}."
        exit 1
      fi
    done
    echo "::error::Could not push the data commit to ${branch} after 3 attempts."
    exit 1
    ;;

  *) usage ;;
esac
