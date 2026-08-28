from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.domain.v20180808 import domain_client, models

from domain_watch.rate_limit import RateLimiter, RateLimitPolicy

DOMAIN_ENDPOINT = "domain.tencentcloudapi.com"
BALANCE_PAY_MODE = 1
AUTO_RENEW_DISABLED = 0
LOCK_DISABLED = 0
CHECK_REQUESTS_PER_SECOND = 9.0
REGISTER_REQUESTS_PER_SECOND = 1.0
DETAIL_REQUESTS_PER_SECOND = 10.0
DETAIL_LIMIT = 20


@dataclass(frozen=True, kw_only=True)
class TencentDomainResult:
    domain: str
    available: bool
    reason: str
    premium: bool | None
    black_word: bool | None
    price: int | None
    real_price: int | None
    request_id: str | None


@dataclass(frozen=True)
class RegistrationSubmission:
    log_id: int
    request_id: str | None


@dataclass(frozen=True)
class RegistrationStatus:
    domain: str
    status: str
    reason: str | None = None


class DomainRegisterConfig(Protocol):
    @property
    def template_id(self) -> str: ...

    @property
    def period(self) -> int: ...


class TencentDomainClient(Protocol):
    def check_domain(self, domain: str, period: int) -> TencentDomainResult: ...

    def submit_registration(
        self,
        domain: str,
        config: DomainRegisterConfig,
    ) -> RegistrationSubmission: ...

    def registration_status(self, log_id: int, domain: str) -> RegistrationStatus: ...


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

    def submit_registration(
        self,
        domain: str,
        config: DomainRegisterConfig,
    ) -> RegistrationSubmission:
        request = build_registration_request(domain, config)
        response = self._client.CreateDomainBatch(request)
        log_id = getattr(response, "LogId", None)
        if not isinstance(log_id, int):
            raise RuntimeError("CreateDomainBatch response is missing integer LogId")
        return RegistrationSubmission(log_id, getattr(response, "RequestId", None))

    def registration_status(self, log_id: int, domain: str) -> RegistrationStatus:
        request = models.DescribeBatchOperationLogDetailsRequest()
        request.LogId = log_id
        request.Offset = 0
        request.Limit = DETAIL_LIMIT
        response = self._client.DescribeBatchOperationLogDetails(request)
        details = getattr(response, "DomainBatchDetailSet", None) or []
        for detail in details:
            if getattr(detail, "Domain", None) == domain:
                return RegistrationStatus(
                    domain=domain,
                    status=getattr(detail, "Status", ""),
                    reason=getattr(detail, "Reason", None),
                )
        raise RuntimeError(f"Registration log {log_id} has no detail for {domain}")


def build_registration_request(
    domain: str,
    config: DomainRegisterConfig,
) -> models.CreateDomainBatchRequest:
    request = models.CreateDomainBatchRequest()
    request.TemplateId = config.template_id
    request.Period = config.period
    request.Domains = [domain]
    request.PayMode = BALANCE_PAY_MODE
    request.AutoRenewFlag = AUTO_RENEW_DISABLED
    request.UpdateProhibition = LOCK_DISABLED
    request.TransferProhibition = LOCK_DISABLED
    request.ChannelFrom = "pc"
    request.OrderFrom = "common"
    return request


class RateLimitedTencentClient:
    def __init__(self, client: TencentDomainClient, limiter: RateLimiter) -> None:
        self._client = client
        self._limiter = limiter

    def check_domain(self, domain: str, period: int) -> TencentDomainResult:
        self._limiter.wait("tencent:check", RateLimitPolicy(CHECK_REQUESTS_PER_SECOND))
        return self._client.check_domain(domain, period)

    def submit_registration(
        self,
        domain: str,
        config: DomainRegisterConfig,
    ) -> RegistrationSubmission:
        self._limiter.wait("tencent:register", RateLimitPolicy(REGISTER_REQUESTS_PER_SECOND))
        return self._client.submit_registration(domain, config)

    def registration_status(self, log_id: int, domain: str) -> RegistrationStatus:
        self._limiter.wait("tencent:detail", RateLimitPolicy(DETAIL_REQUESTS_PER_SECOND))
        return self._client.registration_status(log_id, domain)
