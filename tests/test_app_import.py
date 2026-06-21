from litellm_proxy_headroom.app import app


def test_app_import_exposes_litellm_proxy_app() -> None:
    assert app is not None


def test_headroom_middleware_is_registered() -> None:
    middleware_names = {middleware.cls.__name__ for middleware in app.user_middleware}

    assert "CompressionMiddleware" in middleware_names


def test_headroom_routes_are_mounted_before_dynamic_mcp_routes() -> None:
    route_paths = [getattr(route, "path", None) for route in app.routes]

    assert "/headroom/mcp" in route_paths
    assert "/headroom" in route_paths
    assert route_paths.index("/headroom/mcp") < route_paths.index("/headroom")
    assert route_paths.index("/headroom") < route_paths.index("/{mcp_server_name}/mcp")
