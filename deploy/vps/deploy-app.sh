#!/usr/bin/env bash
# Deploy one tested main commit to the tipping-point-app container.
# Installed root-owned as /usr/local/sbin/tipping-point-app-deploy and run
# through a restricted sudo rule. See docs/DEPLOY-VPS.md.
#
# Scope: this script creates, replaces and removes only the containers
# tipping-point-app, tipping-point-app-candidate and tipping-point-app-previous
# and images tagged tipping-point-app:<commit>. It keeps the tipping-point-data
# volume and never changes another container, volume, network or route.
set -Eeuo pipefail
umask 077

repo_url=https://github.com/r2l2pj/tipping-point.git
app=tipping-point-app
candidate=tipping-point-app-candidate
previous=tipping-point-app-previous
volume=tipping-point-data
host=tippingpoint.l17s.dev

die() {
  echo "$*" >&2
  exit 1
}

# Name of the volume a container mounts at /app/.data (empty when none).
data_volume_of() {
  docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/.data"}}{{.Name}}{{end}}{{end}}' "$1"
}

# Fetch the commit history of main without file contents, prove the commit is
# on main, then check out only the image's build inputs. The whole tree is
# about 1 GB; the build needs app/, Dockerfile and .dockerignore.
fetch_source() {
  local sha=$1 repo=$2
  git init -q "$repo"
  git -C "$repo" remote add origin "$repo_url"
  git -C "$repo" config remote.origin.promisor true
  git -C "$repo" config remote.origin.partialclonefilter tree:0
  git -C "$repo" fetch -q --filter=tree:0 origin '+refs/heads/main:refs/remotes/origin/main'
  if ! git -C "$repo" merge-base --is-ancestor "$sha" origin/main 2>/dev/null; then
    echo "$sha is not on main; refusing to deploy it." >&2
    return 1
  fi
  git -C "$repo" sparse-checkout set --no-cone /Dockerfile /.dockerignore /app/
  git -C "$repo" checkout -q --detach "$sha"
}

# Compare the commit with the one the running container was built from (its
# image tag). Prints deploy, same or older. A newer commit on main does not
# cancel this deploy: main also receives commits that never run app-ci, so
# "skip unless newest" could leave a tested app change undeployed for good.
deploy_order() {
  local sha=$1 repo=$2 current
  current=$(docker inspect -f '{{.Config.Image}}' "$app")
  current=${current#tipping-point-app:}
  if [[ ! "$current" =~ ^[0-9a-f]{40}$ ]]; then
    echo deploy # started by hand: no commit to compare with
  elif [[ "$current" == "$sha" ]]; then
    echo same
  elif git -C "$repo" merge-base --is-ancestor "$current" "$sha" 2>/dev/null; then
    echo deploy
  elif git -C "$repo" merge-base --is-ancestor "$sha" "$current" 2>/dev/null; then
    echo older
  else
    echo deploy # running commit is not on main any more; this one is
  fi
}

# Remove images of earlier deploys. Docker refuses to remove an image that a
# container still uses, so the rollback container keeps its image.
prune_images() {
  local keep=$1 ref
  while read -r ref; do
    [[ "$ref" =~ ^tipping-point-app:[0-9a-f]{40}$ && "$ref" != "$keep" ]] || continue
    docker image rm "$ref" >/dev/null 2>&1 || true
  done < <(docker image ls --format '{{.Repository}}:{{.Tag}}' tipping-point-app)
}

cleanup() {
  local rc=$?
  trap - EXIT
  if (( rc != 0 )); then
    if [[ "$state" == renamed || "$state" == new ]]; then
      docker rm -f "$app" >/dev/null 2>&1 || true
      docker rename "$previous" "$app" && docker start "$app" >/dev/null || \
        echo "URGENT: rollback failed; restore $previous manually." >&2
    elif [[ "$state" == stopping ]]; then
      docker start "$app" >/dev/null || echo "URGENT: restart $app manually." >&2
    fi
  fi
  if (( candidate_created )); then docker rm -f "$candidate" >/dev/null 2>&1 || true; fi
  rm -rf -- "$workdir"
  exit "$rc"
}

wait_healthy() {
  local status i
  for (( i=0; i<45; i++ )); do
    status=$(docker inspect -f '{{.State.Health.Status}}' "$1")
    [[ "$status" == healthy ]] && return 0
    [[ "$status" == unhealthy ]] && return 1
    sleep 2
  done
  return 1
}

main() {
  if (( $# != 1 )) || [[ ! "$1" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Expected exactly one 40-character commit SHA." >&2
    exit 2
  fi
  if (( EUID != 0 )); then
    echo "Run via the root-owned, restricted sudo command." >&2
    exit 2
  fi

  sha=$1
  image="tipping-point-app:$sha"
  workdir=$(mktemp -d /var/lib/tipping-point-app-deploy/build.XXXXXXXX)
  state=untouched
  candidate_created=0
  trap cleanup EXIT

  # Refuse a different app, volume or route before changing production.
  [[ "$(data_volume_of "$app")" == "$volume" ]] ||
    die "$app does not mount the $volume volume at /app/.data; refusing."
  [[ "$(docker inspect -f '{{index .Config.Labels "caddy"}}' tipping-point-route)" == "$host" ]] ||
    die "tipping-point-route does not serve $host; refusing."
  [[ "$(docker inspect -f '{{index .Config.Labels "caddy.reverse_proxy"}}' tipping-point-route)" == "$app:3000" ]] ||
    die "tipping-point-route does not proxy to $app:3000; refusing."
  if docker container inspect "$candidate" >/dev/null 2>&1; then
    die "Candidate name already in use; refusing to remove it."
  fi

  fetch_source "$sha" "$workdir/repo"
  case "$(deploy_order "$sha" "$workdir/repo")" in
    same)
      echo "$sha is already deployed; nothing to do."
      exit 0
      ;;
    older)
      echo "A newer commit is already deployed; skipping $sha."
      exit 0
      ;;
  esac

  docker build -t "$image" "$workdir/repo"
  docker run -d --name "$candidate" --network caddy_proxy --memory 1g \
    --cap-drop ALL --security-opt no-new-privileges "$image" >/dev/null
  candidate_created=1
  wait_healthy "$candidate"
  docker exec "$candidate" node -e \
    "(async()=>{for(const p of ['/api/health','/api/events','/']){const r=await fetch('http://127.0.0.1:3000'+p);if(!r.ok||(p==='/api/health'&&!(await r.json()).ok))process.exitCode=1}})().catch(()=>process.exitCode=1)"
  docker rm -f "$candidate" >/dev/null
  candidate_created=0

  # The old rollback container is ours when it is stopped and mounts our data
  # volume. A container started by hand has no label, so do not ask for one.
  if docker container inspect "$previous" >/dev/null 2>&1; then
    [[ "$(docker inspect -f '{{.State.Status}}' "$previous")" == exited ]] ||
      die "$previous is not stopped; refusing to remove it."
    [[ "$(data_volume_of "$previous")" == "$volume" ]] ||
      die "$previous does not mount the $volume volume; refusing to remove it."
    docker rm "$previous" >/dev/null
  fi

  state=stopping
  docker stop -t 20 "$app" >/dev/null
  docker rename "$app" "$previous"
  state=renamed
  docker run -d --name "$app" --network caddy_proxy --memory 1g \
    --restart unless-stopped --label fe.tipping-point.app=true \
    --cap-drop ALL --security-opt no-new-privileges \
    --mount "source=$volume,target=/app/.data" "$image" >/dev/null
  state=new
  wait_healthy "$app"
  docker exec "$app" node -e \
    "fetch('http://127.0.0.1:3000/api/health').then(async r=>process.exit(r.ok&&(await r.json()).ok?0:1)).catch(()=>process.exit(1))"
  local i
  for (( i=0; i<10; i++ )); do
    if curl -fsS -o /dev/null "https://$host/api/health" && \
       curl -fsS -o /dev/null "https://$host/"; then
      state=deployed
      prune_images "$image"
      echo "Deployed $sha; previous container stopped for rollback."
      exit 0
    fi
    sleep 3
  done
  echo "Public health check failed; restoring previous container." >&2
  exit 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
