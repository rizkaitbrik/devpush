import asyncio
import aiodocker
import base64
import logging
from sqlalchemy import select, true
from sqlalchemy.orm import joinedload
from pathlib import Path
import shlex

# Detection script that runs inside the buildpack container to find the web
# entry point when no Procfile exists. Embedded via base64 to avoid shell
# escaping issues. Covers all major Heroku buildpack runtimes and handles
# nested project structures (src/ layout, packages, etc.).
_DETECT_ENTRYPOINT_SCRIPT = (
    "import glob, json, os, re, sys\n"
    "\n"
    "APP = '/app'\n"
    "PORT = os.environ.get('PORT', '8000')\n"
    "\n"
    "SKIP = {\n"
    "    'venv', '.venv', '__pycache__', 'node_modules', '.git',\n"
    "    'tests', 'test', 'migrations', '.pytest_cache', 'dist', 'build',\n"
    "    '.mypy_cache', 'htmlcov', 'static', 'public', 'media',\n"
    "}\n"
    "\n"
    # Priority by filename convention — agnostic of which framework is used.
    # asgi.py / wsgi.py are universal conventions; then common entry point names.
    "PRIORITY = ['asgi.py', 'wsgi.py', 'main.py', 'app.py', 'server.py',\n"
    "            'application.py', 'run.py', 'api.py']\n"
    "\n"
    # Derive the import root by walking up the __init__.py chain.
    # Works for any layout: flat, src/, nested packages, etc.
    "def import_root(fpath):\n"
    "    d = os.path.dirname(fpath)\n"
    "    while d not in (APP, '/'):\n"
    "        if not os.path.exists(os.path.join(d, '__init__.py')):\n"
    "            return d\n"
    "        d = os.path.dirname(d)\n"
    "    return APP\n"
    "\n"
    "def to_module(fpath):\n"
    "    root = import_root(fpath)\n"
    "    rel = os.path.relpath(fpath, root).replace('\\\\', '/')\n"
    "    parts = rel.replace('.py', '').split('/')\n"
    "    if parts[-1] == '__init__':\n"
    "        parts = parts[:-1]\n"
    "    return '.'.join(parts), root\n"
    "\n"
    "def file_priority(fname):\n"
    "    try:\n"
    "        return len(PRIORITY) - PRIORITY.index(fname)\n"
    "    except ValueError:\n"
    "        return 0\n"
    "\n"
    "def walk_py():\n"
    "    for root, dirs, files in os.walk(APP):\n"
    "        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.'))\n"
    "        for f in files:\n"
    "            if f.endswith('.py'):\n"
    "                yield file_priority(f), os.path.join(root, f)\n"
    "\n"
    # Find the app variable name without knowing the framework.
    # Looks for common variable names first, then any top-level callable result.
    "def app_var(content):\n"
    "    for name in ('app', 'application', 'server', 'api', 'create_app'):\n"
    "        if re.search(rf'^\\s*{name}\\s*=', content, re.M):\n"
    "            return name\n"
    "    m = re.search(r'^\\s*([a-zA-Z_]\\w*)\\s*=\\s*\\w+\\s*\\(', content, re.M)\n"
    "    return m.group(1) if m else 'app'\n"
    "\n"
    "def find_python():\n"
    # Detect what servers the buildpack actually installed — not which framework.
    "    bin_dir = '/app/.heroku/python/bin'\n"
    "    has_uvicorn = os.path.exists(os.path.join(bin_dir, 'uvicorn'))\n"
    "    has_gunicorn = os.path.exists(os.path.join(bin_dir, 'gunicorn'))\n"
    "    if not has_uvicorn and not has_gunicorn:\n"
    # Fall back to system PATH
    "        import shutil\n"
    "        has_uvicorn = bool(shutil.which('uvicorn'))\n"
    "        has_gunicorn = bool(shutil.which('gunicorn'))\n"
    "    candidates = []\n"
    "    for score, fpath in sorted(walk_py(), reverse=True):\n"
    "        fname = os.path.basename(fpath)\n"
    "        try:\n"
    "            content = open(fpath, errors='ignore').read()\n"
    "        except OSError:\n"
    "            continue\n"
    "        mod, app_dir = to_module(fpath)\n"
    "        var = app_var(content)\n"
    # asgi.py → always ASGI regardless of framework
    "        if fname == 'asgi.py':\n"
    "            candidates.append((score + 20, mod, var, 'uvicorn', app_dir))\n"
    # wsgi.py → always WSGI regardless of framework
    "        elif fname == 'wsgi.py':\n"
    "            candidates.append((score + 15, mod, var, 'gunicorn', app_dir))\n"
    # Other named entry points: use whichever server is installed
    "        elif fname in PRIORITY and (has_uvicorn or has_gunicorn):\n"
    "            srv = 'uvicorn' if has_uvicorn else 'gunicorn'\n"
    "            candidates.append((score, mod, var, srv, app_dir))\n"
    "    if not candidates:\n"
    "        return None\n"
    "    candidates.sort(reverse=True)\n"
    "    _, mod, var, srv, app_dir = candidates[0]\n"
    "    if srv == 'uvicorn':\n"
    "        return f'web: uvicorn {mod}:{var} --app-dir {app_dir} --host 0.0.0.0 --port {PORT}', f'Python (uvicorn)'\n"
    "    return f'web: gunicorn {mod}:{var} --chdir {app_dir} --bind 0.0.0.0:{PORT}', f'Python (gunicorn)'\n"
    "\n"
    "def find_django():\n"
    "    for _, fpath in walk_py():\n"
    "        if os.path.basename(fpath) == 'wsgi.py':\n"
    "            mod, app_dir = to_module(fpath)\n"
    "            return f'web: gunicorn {mod} --chdir {app_dir} --bind 0.0.0.0:{PORT}', 'Django'\n"
    "    for root, dirs, files in os.walk(APP):\n"
    "        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.')]\n"
    "        if 'settings.py' in files:\n"
    "            _, app_dir = to_module(os.path.join(root, 'settings.py'))\n"
    "            pkg = os.path.basename(root)\n"
    "            return f'web: gunicorn {pkg}.wsgi --chdir {app_dir} --bind 0.0.0.0:{PORT}', 'Django (settings)'\n"
    "    return None\n"
    "\n"
    "def find_node():\n"
    "    pkg = os.path.join(APP, 'package.json')\n"
    "    try:\n"
    "        data = json.load(open(pkg))\n"
    "        if data.get('scripts', {}).get('start'):\n"
    "            return 'web: npm start', 'Node.js (npm start)'\n"
    "        main = data.get('main')\n"
    "        if main and os.path.exists(os.path.join(APP, main)):\n"
    "            return f'web: node {main}', 'Node.js (main)'\n"
    "    except (OSError, json.JSONDecodeError):\n"
    "        pass\n"
    "    for entry in ['index.js', 'server.js', 'app.js', 'src/index.js']:\n"
    "        if os.path.exists(os.path.join(APP, entry)):\n"
    "            return f'web: node {entry}', f'Node.js ({entry})'\n"
    "    return 'web: npm start', 'Node.js (fallback)'\n"
    "\n"
    "def find_go():\n"
    "    for search_dir in [os.path.join(APP, 'bin'), APP]:\n"
    "        try:\n"
    "            for name in os.listdir(search_dir):\n"
    "                fp = os.path.join(search_dir, name)\n"
    "                if os.path.isfile(fp) and os.access(fp, os.X_OK) and '.' not in name:\n"
    "                    return f'web: {fp}', f'Go (binary: {name})'\n"
    "        except OSError:\n"
    "            pass\n"
    "    return 'web: go run .', 'Go (source)'\n"
    "\n"
    "def find_rust():\n"
    "    try:\n"
    "        content = open(os.path.join(APP, 'Cargo.toml'), errors='ignore').read()\n"
    "        m = re.search(r'^name\\s*=\\s*[\"\\']([^\"\\']+)[\"\\']', content, re.M)\n"
    "        name = m.group(1) if m else 'app'\n"
    "    except OSError:\n"
    "        name = 'app'\n"
    "    return f'web: ./target/release/{name}', f'Rust ({name})'\n"
    "\n"
    "def find_java():\n"
    "    jars = glob.glob(os.path.join(APP, 'target', '*.jar'))\n"
    "    jars += glob.glob(os.path.join(APP, 'build', 'libs', '*.jar'))\n"
    "    jars = [j for j in jars if not j.endswith('-sources.jar')]\n"
    "    if jars:\n"
    "        return f'web: java -jar {jars[0]} --server.port={PORT}', 'Java (jar)'\n"
    "    return f'web: java -jar target/*.jar --server.port={PORT}', 'Java (no jar found)'\n"
    "\n"
    "result = label = None\n"
    "\n"
    "if os.path.exists(os.path.join(APP, 'manage.py')):\n"
    "    out = find_django()\n"
    "    if out: result, label = out\n"
    "\n"
    "if not result:\n"
    "    has_py = any(True for _ in zip(walk_py(), range(1)))\n"
    "    if has_py:\n"
    "        out = find_python()\n"
    "        if out: result, label = out\n"
    "\n"
    "if not result and os.path.exists(os.path.join(APP, 'package.json')):\n"
    "    result, label = find_node()\n"
    "\n"
    "if not result and os.path.exists(os.path.join(APP, 'config.ru')):\n"
    "    result = f'web: bundle exec rackup config.ru -p {PORT} --host 0.0.0.0'\n"
    "    label = 'Ruby/Rack'\n"
    "\n"
    "if not result and os.path.exists(os.path.join(APP, 'Gemfile')):\n"
    "    try:\n"
    "        gem = open(os.path.join(APP, 'Gemfile'), errors='ignore').read().lower()\n"
    "        if 'rails' in gem:\n"
    "            result = f'web: bundle exec rails server -p {PORT} -b 0.0.0.0'\n"
    "            label = 'Ruby/Rails'\n"
    "    except OSError:\n"
    "        pass\n"
    "\n"
    "if not result and os.path.exists(os.path.join(APP, 'go.mod')):\n"
    "    result, label = find_go()\n"
    "\n"
    "if not result and os.path.exists(os.path.join(APP, 'Cargo.toml')):\n"
    "    result, label = find_rust()\n"
    "\n"
    "if not result and (\n"
    "    os.path.exists(os.path.join(APP, 'pom.xml')) or\n"
    "    os.path.exists(os.path.join(APP, 'build.gradle'))\n"
    "):\n"
    "    result, label = find_java()\n"
    "\n"
    "if not result and (\n"
    "    os.path.exists(os.path.join(APP, 'composer.json')) or\n"
    "    any(f.endswith('.php') for f in os.listdir(APP) if os.path.isfile(os.path.join(APP, f)))\n"
    "):\n"
    "    result = 'web: vendor/bin/heroku-php-apache2'\n"
    "    label = 'PHP'\n"
    "\n"
    "if not result and os.path.exists(os.path.join(APP, 'build.sbt')):\n"
    "    result = 'web: target/start'\n"
    "    label = 'Scala'\n"
    "\n"
    "if result:\n"
    "    print(f'[detect] {label}: {result}', file=sys.stderr)\n"
    "    print(result)\n"
    "else:\n"
    "    print('[detect] WARNING: could not detect runtime', file=sys.stderr)\n"
    "    sys.exit(1)\n"
)

from models import Alias, Deployment, Project
from db import AsyncSessionLocal
from dependencies import (
    get_redis_client,
    get_github_installation_service,
)
from config import get_settings
from arq.connections import ArqRedis
from services.deployment import DeploymentService
from services.registry import RegistryService
from services.loki import LokiService

logger = logging.getLogger(__name__)


async def _push_loki_log(
    loki: LokiService,
    deployment: Deployment,
    message: str,
    level: str | None = None,
) -> None:
    labels = {
        "project_id": deployment.project_id,
        "deployment_id": deployment.id,
        "environment_id": deployment.environment_id,
        "branch": deployment.branch,
        "stream": "stdout",
    }
    line = f"{level}: {message}" if level else message
    try:
        await loki.push_log(labels, line)
    except Exception as exc:
        logger.warning("Failed to push log to Loki: %s", exc)


async def start_deployment(ctx, deployment_id: str):
    """Starts a deployment."""
    container = None
    loki: LokiService | None = None
    try:
        settings = get_settings()
        redis_client = get_redis_client()
        log_prefix = f"[DeployStart:{deployment_id}]"
        logger.info(f"{log_prefix} Starting deployment")

        github_installation_service = get_github_installation_service()

        async with AsyncSessionLocal() as db:
            deployment = (
                await db.execute(
                    select(Deployment)
                    .options(joinedload(Deployment.project).joinedload(Project.team))
                    .where(Deployment.id == deployment_id)
                )
            ).scalar_one()
            loki = LokiService()

            container = None
            async with aiodocker.Docker(url=settings.docker_host) as docker_client:
                # Mark deployment as in-progress
                await DeploymentService.update_status(
                    db,
                    deployment,
                    status="prepare",
                    redis_client=redis_client,
                )

                # Prepare environment variables
                env_vars_dict = DeploymentService().get_runtime_env_vars(
                    deployment, settings
                )
                mounts = await DeploymentService().get_runtime_mounts(
                    deployment, db, settings
                )

                config = deployment.config or {}

                # Prepare commands
                commands = []

                is_buildpack = config.get("runner") == "buildpack"
                if is_buildpack:
                    env_vars_dict["PORT"] = "8000"

                # Step 1: Clone the repository
                commands.append(
                    f"echo 'Cloning {deployment.repo_full_name} (Branch: {deployment.branch}, Commit: {deployment.commit_sha[:7]})'"
                )
                github_installation = (
                    await github_installation_service.refresh(
                        deployment.project.vcs_installation_id, db
                    )
                )
                env_vars_dict["DEVPUSH_GITHUB_TOKEN"] = github_installation.token

                if is_buildpack:
                    clone_cmd = (
                        "mkdir -p /tmp/app && "
                        "printf '%s\n' "
                        "'#!/bin/sh' "
                        '\'case "$1" in *Username*) echo "x-access-token";; *) echo "$DEVPUSH_GITHUB_TOKEN";; esac\' '
                        "> /tmp/devpush-git-askpass && "
                        "chmod 700 /tmp/devpush-git-askpass && "
                        "export GIT_ASKPASS=/tmp/devpush-git-askpass GIT_TERMINAL_PROMPT=0 && "
                        f"git -C /tmp/app init -q && "
                        f"git -C /tmp/app fetch -q --depth 1 https://github.com/{deployment.repo_full_name}.git {deployment.commit_sha} && "
                        "git -C /tmp/app checkout -q FETCH_HEAD && "
                        "unset GIT_ASKPASS GIT_TERMINAL_PROMPT DEVPUSH_GITHUB_TOKEN && "
                        "rm -f /tmp/devpush-git-askpass"
                    )
                else:
                    clone_cmd = (
                        "git init -q && "
                        "printf '%s\n' "
                        "'#!/bin/sh' "
                        '\'case "$1" in *Username*) echo "x-access-token";; *) echo "$DEVPUSH_GITHUB_TOKEN";; esac\' '
                        "> /tmp/devpush-git-askpass && "
                        "chmod 700 /tmp/devpush-git-askpass && "
                        "export GIT_ASKPASS=/tmp/devpush-git-askpass GIT_TERMINAL_PROMPT=0 && "
                        f"git fetch -q --depth 1 https://github.com/{deployment.repo_full_name}.git {deployment.commit_sha} && "
                        "git checkout -q FETCH_HEAD && "
                        "unset GIT_ASKPASS GIT_TERMINAL_PROMPT DEVPUSH_GITHUB_TOKEN && "
                        "rm -f /tmp/devpush-git-askpass"
                    )
                commands.append(clone_cmd)

                # Step 2: Change root directory (non-buildpack only)
                normalized_root_directory = (
                    deployment.config.get("root_directory", "")
                    .strip()
                    .lstrip("./")
                    .strip("/")
                )
                if not is_buildpack and normalized_root_directory not in ("", ".", "./"):
                    quoted_root_directory = shlex.quote(normalized_root_directory)
                    commands.append(
                        f"echo 'Changing root directory to {normalized_root_directory}'"
                    )
                    commands.append(
                        f"test -d {quoted_root_directory} || {{ printf '\\033[31mError: root directory %s not found\\033[0m\\n' {quoted_root_directory} 1>&2; exit 1; }}"
                    )
                    commands.append(f"cd {quoted_root_directory}")

                # Step 3: Install dependencies / build
                if is_buildpack:
                    commands.append("echo 'Running buildpack detection and build...'")
                    # herokuish exits non-zero when no Procfile/process types are
                    # found — that's expected here, so we must not let it abort
                    # the rest of the chain.
                    commands.append("herokuish buildpack build || true")
                    # If herokuish didn't produce a Procfile, run the detection
                    # script to find the web entry point. The script is base64-
                    # encoded to avoid shell-quoting issues and injected at
                    # runtime rather than baked into the image.
                    _script_b64 = base64.b64encode(
                        _DETECT_ENTRYPOINT_SCRIPT.encode()
                    ).decode()
                    commands.append(
                        f"if [ ! -f /app/Procfile ]; then "
                        f"echo 'No Procfile found, running entry-point detection...' && "
                        f"echo '{_script_b64}' | base64 -d > /tmp/_detect_entrypoint.py && "
                        f"PROCFILE_LINE=$(python3 /tmp/_detect_entrypoint.py) && "
                        f"echo \"$PROCFILE_LINE\" > /app/Procfile && "
                        f"echo \"Generated Procfile: $(cat /app/Procfile)\"; "
                        f"fi"
                    )
                elif deployment.config.get("build_command"):
                    commands.append("echo 'Installing dependencies...'")
                    commands.append(f"( {deployment.config.get('build_command')} )")

                # Step 4: Pre-deploy command
                if deployment.config.get("pre_deploy_command"):
                    commands.append("echo 'Running pre-deploy command...'")
                    commands.append(
                        f"( {deployment.config.get('pre_deploy_command')} )"
                    )

                # Step 5: Start the application
                commands.append("echo 'Starting application...'")
                if is_buildpack:
                    commands.append("herokuish procfile start web")
                else:
                    commands.append(f"( {deployment.config.get('start_command')} )")

                # Setup container configuration
                container_name = f"runner-{deployment.id[:7]}"
                router = f"deployment-{deployment.id}"

                labels = {
                    "traefik.enable": "true",
                    f"traefik.http.routers.{router}.rule": f"Host(`{deployment.slug}.{settings.deploy_domain}`)",
                    f"traefik.http.routers.{router}.service": f"{router}@docker",
                    f"traefik.http.routers.{router}.priority": "10",
                    f"traefik.http.services.{router}.loadbalancer.server.port": "8000",
                    "traefik.docker.network": "devpush_runner",
                    "devpush.deployment_id": deployment.id,
                    "devpush.project_id": deployment.project_id,
                    "devpush.environment_id": deployment.environment_id,
                    "devpush.branch": deployment.branch,
                }

                if settings.url_scheme == "https":
                    labels.update(
                        {
                            f"traefik.http.routers.{router}.entrypoints": "websecure",
                            f"traefik.http.routers.{router}.tls": "true",
                            f"traefik.http.routers.{router}.tls.certresolver": "le",
                        }
                    )
                else:
                    labels[f"traefik.http.routers.{router}.entrypoints"] = "web"

                cpus: float | None = settings.default_cpus
                memory_mb: int | None = settings.default_memory_mb

                if settings.allow_custom_cpu and config.get("cpus") is not None:
                    max_cpus = settings.max_cpus
                    assert max_cpus is not None
                    try:
                        override_cpus = float(config.get("cpus"))
                    except (ValueError, TypeError):
                        logger.warning(
                            f"{log_prefix} Invalid CPU override in config; using default."
                        )
                    else:
                        if override_cpus > 0:
                            cpus = min(override_cpus, max_cpus)

                if settings.allow_custom_memory and config.get("memory") is not None:
                    max_memory_mb = settings.max_memory_mb
                    assert max_memory_mb is not None
                    try:
                        override_memory_mb = int(config.get("memory"))
                    except (ValueError, TypeError):
                        logger.warning(
                            f"{log_prefix} Invalid memory override in config; using default."
                        )
                    else:
                        if override_memory_mb > 0:
                            memory_mb = min(override_memory_mb, max_memory_mb)

                runner_image = deployment.image
                if not runner_image:
                    runner_slug = config.get("runner") or config.get("image")
                    if not runner_slug:
                        raise ValueError("Runner not set in deployment config.")
                    registry_state = RegistryService(
                        Path(settings.data_dir) / "registry"
                    ).state
                    runner_image = next(
                        (
                            runner.get("image")
                            for runner in registry_state.runners
                            if runner.get("slug") == runner_slug
                        ),
                        None,
                    )
                if not runner_image:
                    raise ValueError("Runner image not found for deployment.")

                await _push_loki_log(
                    loki,
                    deployment,
                    "Checking runner image availability...",
                )
                try:
                    await docker_client.images.get(runner_image)
                    await _push_loki_log(
                        loki,
                        deployment,
                        f"Runner image already present ({runner_image})",
                    )
                except aiodocker.DockerError as error:
                    if error.status == 404:
                        await _push_loki_log(
                            loki,
                            deployment,
                            f"Pulling runner image ({runner_image})...",
                        )
                        try:
                            await docker_client.images.pull(runner_image)
                            await _push_loki_log(
                                loki,
                                deployment,
                                "Runner image pulled",
                            )
                        except Exception:
                            await _push_loki_log(
                                loki,
                                deployment,
                                "Failed to pull runner image",
                                level="Error",
                            )
                            raise
                    else:
                        raise

                await _push_loki_log(
                    loki,
                    deployment,
                    "Preparing and starting container...",
                )

                # Create and start container
                try:
                    container = await docker_client.containers.create_or_replace(
                        name=container_name,
                        config={
                            "Image": runner_image,
                            "Cmd": ["/bin/sh", "-c", " && ".join(commands)],
                            "Env": [f"{k}={v}" for k, v in env_vars_dict.items()],
                            "WorkingDir": "/app",
                            "Labels": labels,
                            "NetworkingConfig": {
                                "EndpointsConfig": {"devpush_runner": {}}
                            },
                            "HostConfig": {
                                **(
                                    {
                                        "CpuQuota": int(cpus * 100000),
                                        "CpuPeriod": 100000,
                                    }
                                    if cpus is not None and cpus > 0
                                    else {}
                                ),
                                **(
                                    {"Memory": memory_mb * 1024 * 1024}
                                    if memory_mb is not None and memory_mb > 0
                                    else {}
                                ),
                                **({"Binds": mounts} if mounts else {}),
                                "SecurityOpt": ["no-new-privileges:true"],
                                "LogConfig": {
                                    "Type": "json-file",
                                    "Config": {"max-size": "10m", "max-file": "5"},
                                },
                            },
                        },
                    )
                except aiodocker.DockerError as error:
                    queue: ArqRedis = ctx["redis"]
                    err_str = str(error)
                    if "No such image" in err_str or "not found" in err_str.lower():
                        reason = "Runner image not found. Contact your administrator."
                    elif "port is already allocated" in err_str.lower():
                        reason = "Port conflict. Another deployment may be using the same port."
                    else:
                        reason = f"Failed to create container: {error}"
                    await queue.enqueue_job(
                        "fail_deployment",
                        deployment_id,
                        "prepare",
                        reason,
                    )
                    logger.error(
                        f"{log_prefix} Failed to start container for {deployment.id}: {error}"
                    )
                    return

                await container.start()

                # Save container info
                deployment.container_id = container.id
                await DeploymentService.update_status(
                    db,
                    deployment,
                    status="deploy",
                    container_status="running",
                    redis_client=redis_client,
                )
                logger.info(
                    f"{log_prefix} Container {container.id} started. Monitoring..."
                )

    except asyncio.CancelledError:
        logger.info(f"{log_prefix} Deployment canceled.")

        if container:
            try:
                try:
                    await container.stop()
                except Exception:
                    pass
                queue: ArqRedis = ctx["redis"]
                await queue.enqueue_job(
                    "delete_container",
                    deployment_id,
                    _defer_by=settings.container_delete_grace_seconds,
                )
            except Exception as e:
                logger.error(f"{log_prefix} Error cleaning up container: {e}")

            try:
                async with AsyncSessionLocal() as db:
                    deployment = await db.get(Deployment, deployment_id)
                    if deployment:
                        await DeploymentService.update_status(
                            db,
                            deployment,
                            status="completed",
                            conclusion="canceled",
                            container_status="stopped",
                            redis_client=get_redis_client(),
                        )
            except Exception as e:
                logger.error(f"{log_prefix} Error updating deployment status: {e}")

    except Exception as e:
        queue: ArqRedis = ctx["redis"]
        await queue.enqueue_job(
            "fail_deployment",
            deployment_id,
            "deploy",
            f"Deployment failed unexpectedly: {e}",
        )
        logger.info(f"{log_prefix} Deployment startup failed.", exc_info=True)
    finally:
        if loki:
            await loki.client.aclose()


async def finalize_deployment(ctx, deployment_id: str):
    """Finalizes a deployment, setting up aliases and updating Traefik config."""
    settings = get_settings()
    redis_client = get_redis_client()
    service = DeploymentService()
    log_prefix = f"[DeployFinalize:{deployment_id}]"
    logger.info(f"{log_prefix} Finalizing deployment")

    queue: ArqRedis | None = ctx.get("redis") if isinstance(ctx, dict) else None

    async with AsyncSessionLocal() as db:
        deployment = None
        try:
            deployment = (
                await db.execute(
                    select(Deployment)
                    .options(joinedload(Deployment.project))
                    .where(Deployment.id == deployment_id)
                )
            ).scalar_one()

            if deployment.conclusion == "canceled":
                logger.info(
                    "%s Deployment already canceled; skipping finalize.", log_prefix
                )
                return

            await DeploymentService().setup_aliases(deployment, db, settings)
            await db.commit()

            # Update Traefik dynamic config
            try:
                await DeploymentService().update_traefik_config(
                    deployment.project,
                    db,
                    settings,
                    include_deployment_ids={deployment.id},
                )
            except Exception as e:
                logger.error(f"{log_prefix} Failed to update Traefik config: {e}")

            await service.update_status(
                db,
                deployment,
                status="completed",
                conclusion="succeeded",
                error=None,
                redis_client=redis_client,
            )

            # Cleanup inactive deployments
            queue: ArqRedis = ctx["redis"]
            await queue.enqueue_job(
                "cleanup_inactive_containers", deployment.project_id
            )
            logger.info(
                f"{log_prefix} Inactive deployments cleanup job queued for project {deployment.project_id}."
            )

        except Exception:
            logger.error(f"{log_prefix} Error finalizing deployment.", exc_info=True)
            if queue:
                await queue.enqueue_job(
                    "fail_deployment",
                    deployment_id,
                    "finalize",
                    "Failed to finalize deployment (aliases/routing). The app may still be running.",
                )


async def fail_deployment(
    ctx, deployment_id: str, status: str, reason: str | None = None
):
    """Handles a failed deployment, cleaning up resources."""
    log_prefix = f"[DeployFail:{deployment_id}]"
    logger.info(f"{log_prefix} Handling failed deployment. Reason: {reason}")
    settings = get_settings()
    redis_client = get_redis_client()
    service = DeploymentService()

    async with AsyncSessionLocal() as db:
        deployment = (
            await db.execute(
                select(Deployment)
                .options(joinedload(Deployment.project))
                .where(Deployment.id == deployment_id)
            )
        ).scalar_one()

        if deployment.conclusion == "canceled":
            logger.info(
                "%s Deployment already canceled; skipping fail handler.", log_prefix
            )
            return
        if deployment.conclusion:
            logger.info(
                "%s Deployment already concluded (%s); skipping fail handler.",
                log_prefix,
                deployment.conclusion,
            )
            return

        await service.update_status(
            db,
            deployment,
            status="fail",
            redis_client=redis_client,
        )

        if deployment.container_id and deployment.container_status not in (
            "removed",
            "stopped",
        ):
            try:
                async with aiodocker.Docker(url=settings.docker_host) as docker_client:
                    container = await docker_client.containers.get(
                        deployment.container_id
                    )
                    try:
                        await container.stop()
                    except Exception:
                        pass
                    queue: ArqRedis = ctx["redis"]
                    await queue.enqueue_job(
                        "delete_container",
                        deployment.id,
                        _defer_by=settings.container_delete_grace_seconds,
                    )
                    logger.info(
                        f"{log_prefix} Cleaned up failed container {deployment.container_id}"
                    )
                    await service.update_status(
                        db,
                        deployment,
                        container_status="stopped",
                        emit=False,
                    )

            except aiodocker.DockerError as error:
                if error.status == 404:
                    logger.warning(
                        f"{log_prefix} Container {deployment.container_id} not found, already removed"
                    )
                    await service.update_status(
                        db,
                        deployment,
                        container_status="removed",
                        emit=False,
                    )
                else:
                    logger.error(
                        f"{log_prefix} Docker error cleaning up container {deployment.container_id}: {error}",
                        exc_info=True,
                    )
            except Exception:
                logger.warning(
                    f"{log_prefix} Could not cleanup container {deployment.container_id}.",
                    exc_info=True,
                )

        await service.update_status(
            db,
            deployment,
            status="completed",
            conclusion="failed",
            error={"status": status, "message": reason or "Deployment failed"},
            redis_client=redis_client,
        )
        logger.error(f"{log_prefix} Deployment failed and cleaned up.")


async def delete_container(ctx, deployment_id: str):
    """Delete a deployment container after a grace period."""
    log_prefix = f"[DeleteContainer:{deployment_id}]"
    logger.info(f"{log_prefix} Deleting container")
    settings = get_settings()
    async with AsyncSessionLocal() as db:
        deployment = await db.get(Deployment, deployment_id)
        if not deployment or not deployment.container_id:
            logger.warning(f"{log_prefix} Deployment or container not found")
            return

        try:
            async with aiodocker.Docker(url=settings.docker_host) as docker_client:
                try:
                    container = await docker_client.containers.get(
                        deployment.container_id
                    )
                    try:
                        await container.stop()
                    except Exception:
                        pass
                    await container.delete(force=True)
                    deployment.container_status = "removed"
                    await db.commit()
                except aiodocker.DockerError as error:
                    if error.status == 404:
                        deployment.container_status = "removed"
                        await db.commit()
        except Exception:
            logger.error(
                f"[DeleteContainer:{deployment_id}] Error deleting container.",
                exc_info=True,
            )


async def cleanup_inactive_containers(
    ctx, project_id: str, remove_containers: bool = True
):
    """Stop/remove containers for deployments no longer referenced by aliases."""
    settings = get_settings()

    async with AsyncSessionLocal() as db:
        async with aiodocker.Docker(url=settings.docker_host) as docker_client:
            try:
                # Get project
                result = await db.execute(
                    select(Project).where(Project.id == project_id)
                )
                project = result.scalar_one_or_none()

                if not project:
                    logger.warning(
                        f"[CleanupInactiveContainers:{project_id}] Project not found"
                    )
                    return

                if project.status == "deleted":
                    logger.info(
                        f"[CleanupInactiveContainers:{project_id}] Project deleted, skipping"
                    )
                    return

                logger.info(
                    f"[CleanupInactiveContainers:{project_id}] Starting cleanup for {project.name}"
                )

                # Get active deployment IDs
                active_result = await db.execute(
                    select(Alias.deployment_id)
                    .join(Deployment, Alias.deployment_id == Deployment.id)
                    .where(
                        Deployment.project_id == project_id,
                        Alias.deployment_id.isnot(None),
                    )
                    .union(
                        select(Alias.previous_deployment_id)
                        .join(Deployment, Alias.previous_deployment_id == Deployment.id)
                        .where(
                            Deployment.project_id == project_id,
                            Alias.previous_deployment_id.isnot(None),
                        )
                    )
                )
                active_deployment_ids = set(active_result.scalars().all())

                logger.debug(
                    f"[CleanupInactiveContainers:{project_id}] Active deployments: {active_deployment_ids}"
                )

                # Get inactive deployments with containers
                inactive_result = await db.execute(
                    select(Deployment).where(
                        Deployment.project_id == project_id,
                        Deployment.container_id.isnot(None),
                        Deployment.container_status == "running",
                        Deployment.status == "completed",
                        Deployment.id.notin_(active_deployment_ids)
                        if active_deployment_ids
                        else true(),
                    )
                )
                inactive_deployments = inactive_result.scalars().all()

                stopped_count = 0
                removed_count = 0

                for deployment in inactive_deployments:
                    logger.info(
                        f"[CleanupInactiveContainers:{project_id}] Processing inactive deployment {deployment.id}"
                    )
                    try:
                        if deployment.container_id is None:
                            logger.warning(
                                f"[CleanupInactiveContainers:{project_id}] Deployment {deployment.id} has no container"
                            )
                            continue

                        container = await docker_client.containers.get(
                            deployment.container_id
                        )

                        # Stop container
                        await container.stop()
                        deployment.container_status = "stopped"
                        stopped_count += 1
                        logger.info(
                            f"[CleanupInactiveContainers:{project_id}] Stopped container {deployment.container_id}"
                        )

                        # Remove if requested
                        if remove_containers:
                            await container.delete()
                            deployment.container_status = "removed"
                            removed_count += 1
                            logger.info(
                                f"[CleanupInactiveContainers:{project_id}] Removed container {deployment.container_id}"
                            )

                    except aiodocker.DockerError as error:
                        if error.status == 404:
                            logger.warning(
                                f"[CleanupInactiveContainers:{project_id}] Container {deployment.container_id} not found"
                            )
                            deployment.container_status = None
                        else:
                            logger.error(
                                f"[CleanupInactiveContainers:{project_id}] Docker error: {error}"
                            )
                    except Exception as error:
                        logger.error(
                            f"[CleanupInactiveContainers:{project_id}] Error processing container: {error}"
                        )

                # Commit status updates
                if stopped_count > 0 or removed_count > 0:
                    try:
                        await db.commit()
                        logger.info(
                            f"[CleanupInactiveContainers:{project_id}] Stopped: {stopped_count}, Removed: {removed_count}"
                        )
                    except Exception as e:
                        logger.error(
                            f"[CleanupInactiveContainers:{project_id}] Failed to commit: {e}"
                        )
                        await db.rollback()
                else:
                    logger.info(
                        f"[CleanupInactiveContainers:{project_id}] No inactive containers found"
                    )

            except Exception as error:
                logger.error(
                    f"[CleanupInactiveContainers:{project_id}] Task failed: {error}"
                )
                await db.rollback()
                raise
