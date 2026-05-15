import hmac
import logging
import time
from datetime import datetime, timezone

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings, get_settings
from dependencies import (
    TemplateResponse,
    flash,
    get_db,
    get_gitlab_adapter,
    get_gitlab_installation_service,
    get_gitlab_oauth_client,
    get_queue,
    get_translation as _,
    get_current_user,
)
from integrations.vcs.gitlab import GitLabAdapter, GitLabInstallationService
from integrations.vcs.models import Commit, CommitAuthor
from models import Project, User, UserIdentity
from services.deployment import DeploymentService
from utils.urls import safe_redirect
from utils.user import get_user_by_provider, get_user_vcs_token

router = APIRouter(prefix="/api/gitlab")
logger = logging.getLogger(__name__)


@router.get("/repo-select", name="gitlab_repo_select")
async def gitlab_repo_select(
    request: Request,
    account: str | None = None,
    current_user: User = Depends(get_current_user),
    gitlab_adapter: GitLabAdapter = Depends(get_gitlab_adapter),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    accounts = []
    selected_account = None
    has_token = False

    try:
        token = await get_user_vcs_token(db, current_user, "gitlab")
        if not token:
            has_token = False
        else:
            has_token = True
            account_list = await gitlab_adapter.get_accounts(token)
            accounts = [acc.name for acc in account_list]
            selected_account = account or (accounts[0] if accounts else None)
    except Exception:
        logger.exception("Error fetching GitLab accounts")
        flash(request, _("Error fetching GitLab accounts."), "error")

    return TemplateResponse(
        request=request,
        name="gitlab/partials/_repo-select.html",
        context={
            "current_user": current_user,
            "accounts": accounts,
            "selected_account": selected_account,
            "has_token": has_token,
            "has_gitlab": bool(settings.gitlab_client_id),
        },
    )


@router.get("/repo-list", name="gitlab_repo_list")
async def gitlab_repo_list(
    request: Request,
    current_user: User = Depends(get_current_user),
    gitlab_adapter: GitLabAdapter = Depends(get_gitlab_adapter),
    account: str | None = None,
    query: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    repos = []
    try:
        token = await get_user_vcs_token(db, current_user, "gitlab")
        if token and account:
            account_list = await gitlab_adapter.get_accounts(token)
            selected = next((a for a in account_list if a.name == account), None)
            if selected:
                repos = await gitlab_adapter.get_repositories(token, selected.id, query or "")
    except Exception:
        logger.exception("Error fetching GitLab repositories")
        flash(request, _("Error fetching GitLab repositories."), "error")

    return TemplateResponse(
        request=request,
        name="gitlab/partials/_repo-select-list.html",
        context={"repos": repos},
    )


@router.get("/authorize", name="gitlab_authorize")
async def gitlab_authorize(
    request: Request,
    next: str | None = None,
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    oauth_client=Depends(get_gitlab_oauth_client),
):
    if not oauth_client:
        flash(request, _("GitLab OAuth not configured."), "error")
        redirect_url = safe_redirect(request, next_value=next, referer=request.headers.get("Referer"))
        return RedirectResponse(redirect_url, status_code=303)

    redirect_url = safe_redirect(request, next_value=next, referer=request.headers.get("Referer"))
    request.session["redirect_after_gitlab"] = redirect_url

    return await oauth_client.gitlab.authorize_redirect(
        request, request.url_for("gitlab_authorize_callback")
    )


@router.get("/authorize/callback", name="gitlab_authorize_callback")
async def gitlab_authorize_callback(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    gitlab_adapter: GitLabAdapter = Depends(get_gitlab_adapter),
    gitlab_installation_service: GitLabInstallationService = Depends(get_gitlab_installation_service),
    oauth_client=Depends(get_gitlab_oauth_client),
):
    redirect_url = safe_redirect(
        request,
        next_value=request.session.pop("redirect_after_gitlab", "/"),
        referer=None,
    )

    if not oauth_client:
        flash(request, _("GitLab OAuth not configured."), "error")
        return RedirectResponse(redirect_url, status_code=303)

    try:
        token = await oauth_client.gitlab.authorize_access_token(request)
        access_token = token["access_token"]
        refresh_token = token.get("refresh_token")

        expires_at = None
        expires_in = token.get("expires_in")
        if expires_in:
            created_at = token.get("created_at", int(time.time()))
            expires_at = datetime.fromtimestamp(
                created_at + expires_in, tz=timezone.utc
            ).replace(tzinfo=None)

        gl_user = await gitlab_adapter.get_user_info(access_token)

        existing_user = await get_user_by_provider(db, "gitlab", gl_user.id)
        if existing_user and existing_user.id != current_user.id:
            flash(request, _("This GitLab account is already linked to another user."), "error")
            return RedirectResponse(redirect_url, status_code=303)

        result = await db.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == current_user.id,
                UserIdentity.provider == "gitlab",
            )
        )
        identity = result.scalar_one_or_none()

        if identity:
            identity.access_token = access_token
            if refresh_token:
                identity.refresh_token = refresh_token
            if expires_at:
                identity.token_expires_at = expires_at
            identity.provider_metadata = {"username": gl_user.username, "name": gl_user.name}
        else:
            identity = UserIdentity(
                user_id=current_user.id,
                provider="gitlab",
                provider_user_id=gl_user.id,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=expires_at,
                provider_metadata={"username": gl_user.username, "name": gl_user.name},
            )
            db.add(identity)

        await db.commit()

        # Create/refresh VcsInstallation for the user's personal namespace
        accounts = await gitlab_adapter.get_accounts(access_token)
        personal = next((a for a in accounts if a.name == gl_user.username), None)
        if personal:
            await gitlab_installation_service.get_or_create(
                access_token=access_token,
                provider_account_id=personal.id,
                provider_account_name=personal.name,
                db=db,
                refresh_token=refresh_token,
                token_expires_at=expires_at,
            )

        flash(request, _("GitLab account connected successfully!"), "success")

    except Exception:
        logger.exception("Error connecting GitLab account")
        flash(request, _("Error connecting GitLab account."), "error")

    return RedirectResponse(redirect_url, status_code=303)


async def _verify_gitlab_webhook(
    request: Request, settings: Settings = Depends(get_settings)
) -> dict:
    """Verify the X-Gitlab-Token header and return parsed JSON payload."""
    token = request.headers.get("X-Gitlab-Token", "")
    if not settings.gitlab_webhook_secret:
        raise HTTPException(status_code=500, detail="GitLab webhook secret not configured")
    if not hmac.compare_digest(token, settings.gitlab_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
    return await request.json()


@router.post("/webhook", name="gitlab_webhook")
async def gitlab_webhook(
    request: Request,
    data: dict = Depends(_verify_gitlab_webhook),
    db: AsyncSession = Depends(get_db),
    queue: ArqRedis = Depends(get_queue),
):
    event = request.headers.get("X-Gitlab-Event", "")
    logger.info("Received GitLab webhook event: %s", event)

    try:
        if event == "Push Hook":
            repo_id = data.get("project", {}).get("id")
            if not repo_id:
                return Response(status_code=200)

            result = await db.execute(
                select(Project).where(
                    Project.repo_id == repo_id,
                    Project.status == "active",
                )
            )
            projects = result.scalars().all()

            if not projects:
                logger.info("No active projects for GitLab repo %s", repo_id)
                return Response(status_code=200)

            branch = data.get("ref", "").replace("refs/heads/", "")
            head_commit = data.get("commits", [{}])[0] if data.get("commits") else {}
            commit_data = Commit(
                sha=data.get("after", ""),
                message=head_commit.get("message", ""),
                author=CommitAuthor(
                    name=data.get("user_name", ""),
                    login=data.get("user_username"),
                    date=head_commit.get("timestamp", ""),
                ),
            )

            deployment_service = DeploymentService()
            redis_client = request.app.state.redis_pool

            for project in projects:
                try:
                    deployment = await deployment_service.create(
                        project=project,
                        branch=branch,
                        commit=commit_data,
                        db=db,
                        redis_client=redis_client,
                        trigger="webhook",
                    )
                    job = await queue.enqueue_job("start_deployment", deployment.id)
                    deployment.job_id = job.job_id
                    await db.commit()
                    logger.info(
                        "Deployment %s created for GitLab push on project %s",
                        deployment.id,
                        project.name,
                    )
                except Exception:
                    logger.exception(
                        "Failed to create deployment for project %s", project.name
                    )

        elif event == "System Hook" and data.get("event_name") in ("project_destroy", "project_transfer", "project_rename"):
            # Keep repo_status in sync with GitLab project lifecycle
            action = data.get("event_name", "")
            repo_id = data.get("project_id")
            if repo_id:
                new_status = {
                    "project_destroy": "deleted",
                    "project_transfer": "transferred",
                    "project_rename": "active",
                }.get(action, "active")
                await db.execute(
                    update(Project)
                    .where(Project.repo_id == repo_id)
                    .values(repo_status=new_status)
                )
                if action == "project_rename":
                    path_with_namespace = data.get("path_with_namespace")
                    if path_with_namespace:
                        await db.execute(
                            update(Project)
                            .where(Project.repo_id == repo_id)
                            .values(repo_full_name=path_with_namespace)
                        )
                await db.commit()

    except Exception:
        logger.exception("Error processing GitLab webhook event: %s", event)
        await db.rollback()
        return Response(status_code=500)

    return Response(status_code=200)
