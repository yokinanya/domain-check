from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.domain.v20180808 import domain_client, models

DOMAIN_ENDPOINT = "domain.tencentcloudapi.com"
MANUAL_BALANCE_PAY_MODE = 1
AUTO_RENEW_DISABLED = 0
LOCK_DISABLED = 0


@dataclass(frozen=True)
class TencentDomainResult:
    domain: str
    available: bool
    reason: str
    premium: bool | None
    black_word: bool | None
    price: int | None
    real_price: int | None
    request_id: str | None


class DomainRegisterConfig(Protocol):
    template_id: str
    period: int


class TencentDomainClient(Protocol):
    def check_domain(self, domain: str, period: int) -> TencentDomainResult: ...

    def create_domain_batch(
        self,
        domains: tuple[str, ...],
        config: DomainRegisterConfig,
    ) -> object: ...


class TencentSdkDomainClient:
    def __init__(self, secret_id: str, secret_key: str) -> None:
        cred = credential.Credential(secret_id, secret_key)
        http_profile = HttpProfile()
        http_profile.endpoint = DOMAIN_ENDPOINT
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        self._client = domain_client.DomainClient(cred, "", client_profile)

    def check_domain(self, domain: str, period: int) -> TencentDomainResult:
        request = models.CheckDomainRequest()
        request.DomainName = domain
        request.Period = str(period)
        response = self._client.CheckDomain(request)
        return TencentDomainResult(
            domain=getattr(response, "DomainName", domain),
            available=bool(getattr(response, "Available", False)),
            reason=getattr(response, "Reason", "") or "",
            premium=getattr(response, "Premium", None),
            black_word=getattr(response, "BlackWord", None),
            price=getattr(response, "Price", None),
            real_price=getattr(response, "RealPrice", None),
            request_id=getattr(response, "RequestId", None),
        )

    def create_domain_batch(
        self,
        domains: tuple[str, ...],
        config: DomainRegisterConfig,
    ) -> object:
        request = models.CreateDomainBatchRequest()
        request.TemplateId = config.template_id
        request.Period = config.period
        request.Domains = list(domains)
        request.PayMode = MANUAL_BALANCE_PAY_MODE
        request.AutoRenewFlag = AUTO_RENEW_DISABLED
        request.UpdateProhibition = LOCK_DISABLED
        request.TransferProhibition = LOCK_DISABLED
        request.ChannelFrom = "pc"
        request.OrderFrom = "common"
        return self._client.CreateDomainBatch(request)
