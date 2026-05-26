"""Shared admin router for trace, evaluation, experiment, and dataset APIs."""

from fastapi import APIRouter, Depends

from api.dependencies import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])
