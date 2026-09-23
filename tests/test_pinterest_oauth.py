def test_callback_rejects_missing_credentials(monkeypatch):
    """Test that missing PINTEREST_CLIENT_ID or PINTEREST_CLIENT_SECRET blocks the OAuth flow."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    # Remove both client_id and client_secret from environment
    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.find_checkpoint_by_oauth_state.return_value = cp
    mock_manager.complete_checkpoint.return_value = {"success": True, "checkpoint": {"id": "cp-1"}}
    mock_manager.fail_checkpoint.return_value = {"success": True}

    mock_connector = MagicMock()
    mock_connector.connect_account.return_value = {"success": True, "status": "connected"}
    mock_connector.resume_onboarding.return_value = {"success": True, "status": "awaiting_human", "next_step": "email_verification_required"}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient") as mock_async_client:
        mock_async_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=fake_post)
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 400
        assert "Missing" in resp.json()["detail"]
        # Verify no connection was created (connect_account was not called)
        mock_connector.connect_account.assert_not_called()
        # Verify checkpoint was not completed (complete_checkpoint was not called)
        mock_manager.complete_checkpoint.assert_not_called()
        # Verify no resume was called (resume_onboarding was not called)
        mock_connector.resume_onboarding.assert_not_called()

    # Verify the response is non-success
    assert resp.status_code != 200
    assert "non-success" in resp.text.lower() or "400" in resp.text
