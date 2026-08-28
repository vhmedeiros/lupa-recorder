import pytest

from lupa_recorder.config import SourceConfig
from lupa_recorder.resolve.base import ResolveError
from lupa_recorder.resolve.http_refresh import HttpRefreshResolver, extrair_por_caminho
from lupa_recorder.resolve.static import StaticResolver

FONTE_BASE = {
    "id": 1,
    "slug": "radio-teste",
    "kind": "radio",
    "protocol": "http",
    "url": "http://exemplo.com/stream",
}


class TestExtrairPorCaminho:
    def test_chave_de_topo(self):
        assert extrair_por_caminho({"url": "http://x.com/a"}, "url") == "http://x.com/a"

    def test_caminho_aninhado(self):
        dados = {"data": {"stream": {"url": "http://x.com/b"}}}
        assert extrair_por_caminho(dados, "data.stream.url") == "http://x.com/b"

    def test_chave_ausente_devolve_none(self):
        assert extrair_por_caminho({"outra": "coisa"}, "url") is None

    def test_valor_nao_string_devolve_none(self):
        assert extrair_por_caminho({"url": 123}, "url") is None


class TestStaticResolver:
    async def test_resolve_devolve_a_url_do_config(self):
        fonte = SourceConfig(**FONTE_BASE)
        resultado = await StaticResolver().resolve(fonte)
        assert resultado.urls == ["http://exemplo.com/stream"]

    async def test_sem_url_levanta_erro(self):
        # SourceConfig não deixa construir sem url pra protocol != dvb — pra testar o
        # resolver isolado nesse caso de borda, construímos válido e removemos depois
        # (model_copy não revalida).
        fonte = SourceConfig(**FONTE_BASE).model_copy(update={"url": None})
        with pytest.raises(ResolveError):
            await StaticResolver().resolve(fonte)


class TestHttpRefreshResolver:
    async def test_sem_url_refresh_url_levanta_erro(self):
        fonte = SourceConfig(**FONTE_BASE)  # url_resolver=static por padrão, sem url_refresh_url
        with pytest.raises(ResolveError, match="url_refresh_url"):
            await HttpRefreshResolver().resolve(fonte)

    async def test_resolve_busca_url_fresca(self, monkeypatch):
        fonte = SourceConfig(
            **{
                **FONTE_BASE,
                "url_resolver": "http_refresh",
                "url_refresh_url": "https://exemplo.com/api/fresh",
            }
        )

        def fake_buscar(refresh_url, json_path, timeout_s=10):
            assert refresh_url == "https://exemplo.com/api/fresh"
            assert json_path == "url"
            return "https://cdn.exemplo.com/stream-fresca?token=abc"

        import lupa_recorder.resolve.http_refresh as mod

        monkeypatch.setattr(mod, "_buscar_url_fresca_sync", fake_buscar)

        resultado = await HttpRefreshResolver().resolve(fonte)
        assert resultado.urls == ["https://cdn.exemplo.com/stream-fresca?token=abc"]
