# -*- coding: utf-8 -*-
"""
===================================
信号看板接口（容器 C / N1）
===================================

职责：
1. GET /api/v1/signals/board 自选池量价信号看板
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_system_config_service
from api.v1.schemas.stocks import SignalsBoardResponse
from api.v1.schemas.common import ErrorResponse
from src.services.system_config_service import SystemConfigService
from src.services.signal_board_service import build_board
from api.v1.endpoints.stocks import _read_watchlist_codes  # 复用自选池读取，避免平行实现

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/board",
    response_model=SignalsBoardResponse,
    responses={
        200: {"description": "自选池信号看板（含 degraded 行）"},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="自选池量价信号看板",
    description="对 STOCK_LIST 自选池近实时计算每只标的量价信号，按动作分组返回；单 code 失败仅降级该行。",
)
def get_signals_board(
    days: int = Query(120, ge=1, le=365, description="日历回看天数（与 /history 同源）"),
    refresh: bool = Query(False, description="跳过缓存强制重算"),
    service: SystemConfigService = Depends(get_system_config_service),
) -> SignalsBoardResponse:
    try:
        codes = _read_watchlist_codes(service)
        return SignalsBoardResponse(**build_board(codes, days=days, refresh=refresh))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("信号看板失败 err=%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="信号看板失败") from exc
