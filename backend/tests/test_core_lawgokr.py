import httpx
import pytest

from privframe.core.lawgokr import LawApiAuthError, LawApiUnavailable, LawGoKr, jo_code


@pytest.mark.parametrize(("article", "code"), [("제2조", "000200"), ("제25조의2", "002502"), ("58조의2", "005802"), ("제 10 조 의 3", "001003")])
def test_jo_code(article: str, code: str):
    assert jo_code(article) == code


def test_unregistered_host_raises_an_auth_error():
    body = {"result": "사용자 정보 검증에 실패하였습니다.", "msg": "OPEN API 호출 시 사용자 검증을 위하여 정확한 서버장비의 IP주소 및 도메인주소를 등록해 주세요."}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    with pytest.raises(LawApiAuthError, match="IP"):
        LawGoKr("someone", client=client).search("law", "개인정보 보호법")


def test_search_hits_and_count_whatever_the_wrapper_key():
    body = {"PrecSearch": {"totalCnt": "1", "prec": {"판례일련번호": "228541", "사건번호": "2020다253423"}}}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    result = LawGoKr("someone", client=client).search("prec", nb="2020다253423")
    assert LawGoKr.items(result)[0]["판례일련번호"] == "228541"
    assert LawGoKr.count(result) == 1


def test_public_url_never_contains_the_credential():
    url = LawGoKr.public_url("lawService.do", {"OC": "secret-oc", "target": "prec", "ID": "1"})
    assert "secret-oc" not in url and "target=prec" in url


def refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"cannot reach {request.url}", request=request)


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (lambda request: httpx.Response(302, headers={"location": f"https://www.law.go.kr/sorry/sorry.html?{request.url.query.decode()}"}), "점검"),  # announced maintenance: every address is sent to the notice
        (lambda request: httpx.Response(503), "503"),
        (lambda request: httpx.Response(200, text=f"<html>busy {request.url}</html>"), "JSON"),
        (refused, "ConnectError"),
    ],
)
def test_no_answer_is_its_own_error_and_never_quotes_the_credential(answer, says):
    client = httpx.Client(transport=httpx.MockTransport(answer))
    with pytest.raises(LawApiUnavailable, match=says) as caught:
        LawGoKr("secret-oc", client=client).search("prec", "초상권")
    error = caught.value
    told = [str(error), repr(error), str(error.__cause__), str(error.__context__ if not error.__suppress_context__ else "")]
    assert not any("secret-oc" in text for text in told)
    assert "target=prec" in str(error)  # what was asked is still said, without the credential
