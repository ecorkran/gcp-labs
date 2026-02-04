import pytest
from api.main import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_health_endpoint(client):
    response = client.get('/')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'healthy'

def test_events_endpoint(client):
    response = client.get('/events')
    assert response.status_code == 200
    data = response.get_json()
    assert 'events' in data
    assert 'count' in data
