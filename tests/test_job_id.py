from job_agent_lab.job_id import job_id_from_link


def test_identical_links_same_id():
    url = "https://example.com/jobs/123"
    assert job_id_from_link(url) == job_id_from_link(url)


def test_host_case_and_trailing_slash_same_id():
    a = job_id_from_link("https://Example.COM/jobs/123/")
    b = job_id_from_link("https://example.com/jobs/123")
    assert a == b


def test_different_links_different_ids():
    a = job_id_from_link("https://example.com/jobs/123")
    b = job_id_from_link("https://example.com/jobs/456")
    assert a != b
