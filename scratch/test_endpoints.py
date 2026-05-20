import unittest
from app import app

class TestDashboardIntegrations(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()

    def test_service_worker_route(self):
        """Verify the service worker is served correctly from the root with the required headers."""
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/javascript')
        self.assertEqual(response.headers.get('Service-Worker-Allowed'), '/')

    def test_manifest_file(self):
        """Verify the PWA manifest is served correctly and contains valid JSON."""
        response = self.client.get('/static/manifest.json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/json')
        
        # Verify JSON parsing
        import json
        data = json.loads(response.data)
        self.assertEqual(data.get('short_name'), 'Nivixsa')
        self.assertEqual(data.get('display'), 'standalone')

    def test_icons_exist(self):
        """Verify icons are present at the configured manifest paths."""
        for size in [72, 96, 128, 144, 192, 384, 512]:
            response = self.client.get(f'/static/icons/icon-{size}x{size}.png')
            self.assertEqual(response.status_code, 200, f"Icon {size}x{size} is missing!")

    def test_push_public_key(self):
        """Verify the public key VAPID endpoint returns a valid key."""
        response = self.client.get('/api/push/public-key')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/json')
        
        import json
        data = json.loads(response.data)
        self.assertIn('publicKey', data)
        self.assertEqual(data['publicKey'], 'BN5LkFCS-PDB5k7RxYXFqqmOuxuk6som0P_6vN9wKchrmOwE8m5LC_EQ9KqadiR5mNM0pr23yVE25h-iil_nSGw')

    def test_push_subscribe_unauthorized(self):
        """Verify subscription fails with 401 when user is not logged in."""
        response = self.client.post('/api/push/subscribe', json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/some-token",
            "keys": {
                "p256dh": "some-dh-key",
                "auth": "some-auth-key"
            }
        })
        self.assertEqual(response.status_code, 401)

    def test_push_unsubscribe(self):
        """Verify unsubscription returns success when sending a valid endpoint."""
        response = self.client.post('/api/push/unsubscribe', json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/some-token"
        })
        self.assertEqual(response.status_code, 200)
        
        import json
        data = json.loads(response.data)
        self.assertTrue(data.get("success"))

if __name__ == '__main__':
    unittest.main()
