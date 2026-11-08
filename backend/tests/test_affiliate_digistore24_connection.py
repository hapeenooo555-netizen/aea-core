from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from app.routers import affiliate_digistore24_connection as connection


def test_start_connection_refuses_already_connected_user():
    client = Mock()
    query = client.table.return_value.select.return_value.eq.return_value.limit.return_value
    query.execute.return_value.data = [{"user_id": "user-123"}]

    with patch.object(connection, "_service_client", return_value=client), patch.object(
        connection, "request_user_api_key"
    ) as request_key:
        with pytest.raises(HTTPException) as exc:
            connection.start_connection(user_id="user-123")

    assert exc.value.status_code == 409
    assert "already connected" in exc.value.detail
    request_key.assert_not_called()
    client.table.assert_called_once_with("digistore24_user_connections")
