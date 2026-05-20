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

if __name__ == '__main__':
    unittest.main()
