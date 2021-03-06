#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json

import mock
from oslo_config import cfg
import six
import webob.exc

import heat.api.middleware.fault as fault
import heat.api.openstack.v1.stacks as stacks
from heat.common import exception as heat_exc
from heat.common import identifier
from heat.common import policy
from heat.common import template_format
from heat.common import urlfetch
from heat.rpc import api as rpc_api
from heat.rpc import client as rpc_client
from heat.tests.api.openstack_v1 import tools
from heat.tests import common


class InstantiationDataTest(common.HeatTestCase):

    def test_parse_error_success(self):
        with stacks.InstantiationData.parse_error_check('Garbage'):
            pass

    def test_parse_error(self):
        def generate_error():
            with stacks.InstantiationData.parse_error_check('Garbage'):
                raise ValueError

        self.assertRaises(webob.exc.HTTPBadRequest, generate_error)

    def test_parse_error_message(self):
        # make sure the parser error gets through to the caller.
        bad_temp = '''
heat_template_version: '2013-05-23'
parameters:
  KeyName:
     type: string
    description: bla
        '''

        def generate_error():
            with stacks.InstantiationData.parse_error_check('foo'):
                template_format.parse(bad_temp)

        parse_ex = self.assertRaises(webob.exc.HTTPBadRequest, generate_error)
        self.assertIn('foo', six.text_type(parse_ex))

    def test_stack_name(self):
        body = {'stack_name': 'wibble'}
        data = stacks.InstantiationData(body)
        self.assertEqual('wibble', data.stack_name())

    def test_stack_name_missing(self):
        body = {'not the stack_name': 'wibble'}
        data = stacks.InstantiationData(body)
        self.assertRaises(webob.exc.HTTPBadRequest, data.stack_name)

    def test_template_inline(self):
        template = {'foo': 'bar', 'blarg': 'wibble'}
        body = {'template': template}
        data = stacks.InstantiationData(body)
        self.assertEqual(template, data.template())

    def test_template_string_json(self):
        template = ('{"heat_template_version": "2013-05-23",'
                    '"foo": "bar", "blarg": "wibble"}')
        body = {'template': template}
        data = stacks.InstantiationData(body)
        self.assertEqual(json.loads(template), data.template())

    def test_template_string_yaml(self):
        template = '''HeatTemplateFormatVersion: 2012-12-12
foo: bar
blarg: wibble
'''
        parsed = {u'HeatTemplateFormatVersion': u'2012-12-12',
                  u'blarg': u'wibble',
                  u'foo': u'bar'}

        body = {'template': template}
        data = stacks.InstantiationData(body)
        self.assertEqual(parsed, data.template())

    def test_template_url(self):
        template = {'heat_template_version': '2013-05-23',
                    'foo': 'bar',
                    'blarg': 'wibble'}
        url = 'http://example.com/template'
        body = {'template_url': url}
        data = stacks.InstantiationData(body)

        self.m.StubOutWithMock(urlfetch, 'get')
        urlfetch.get(url).AndReturn(json.dumps(template))
        self.m.ReplayAll()

        self.assertEqual(template, data.template())
        self.m.VerifyAll()

    def test_template_priority(self):
        template = {'foo': 'bar', 'blarg': 'wibble'}
        url = 'http://example.com/template'
        body = {'template': template, 'template_url': url}
        data = stacks.InstantiationData(body)

        self.m.StubOutWithMock(urlfetch, 'get')
        self.m.ReplayAll()

        self.assertEqual(template, data.template())
        self.m.VerifyAll()

    def test_template_missing(self):
        template = {'foo': 'bar', 'blarg': 'wibble'}
        body = {'not the template': template}
        data = stacks.InstantiationData(body)
        self.assertRaises(webob.exc.HTTPBadRequest, data.template)

    def test_parameters(self):
        params = {'foo': 'bar', 'blarg': 'wibble'}
        body = {'parameters': params,
                'encrypted_param_names': [],
                'parameter_defaults': {},
                'resource_registry': {}}
        data = stacks.InstantiationData(body)
        self.assertEqual(body, data.environment())

    def test_environment_only_params(self):
        env = {'parameters': {'foo': 'bar', 'blarg': 'wibble'}}
        body = {'environment': env}
        data = stacks.InstantiationData(body)
        self.assertEqual(env, data.environment())

    def test_environment_and_parameters(self):
        body = {'parameters': {'foo': 'bar'},
                'environment': {'parameters': {'blarg': 'wibble'}}}
        expect = {'parameters': {'blarg': 'wibble',
                                 'foo': 'bar'},
                  'encrypted_param_names': [],
                  'parameter_defaults': {},
                  'resource_registry': {}}
        data = stacks.InstantiationData(body)
        self.assertEqual(expect, data.environment())

    def test_parameters_override_environment(self):
        # This tests that the cli parameters will override
        # any parameters in the environment.
        body = {'parameters': {'foo': 'bar',
                               'tester': 'Yes'},
                'environment': {'parameters': {'blarg': 'wibble',
                                               'tester': 'fail'}}}
        expect = {'parameters': {'blarg': 'wibble',
                                 'foo': 'bar',
                                 'tester': 'Yes'},
                  'encrypted_param_names': [],
                  'parameter_defaults': {},
                  'resource_registry': {}}
        data = stacks.InstantiationData(body)
        self.assertEqual(expect, data.environment())

    def test_environment_bad_format(self):
        env = {'somethingnotsupported': {'blarg': 'wibble'}}
        body = {'environment': json.dumps(env)}
        data = stacks.InstantiationData(body)
        self.assertRaises(webob.exc.HTTPBadRequest, data.environment)

    def test_environment_missing(self):
        env = {'foo': 'bar', 'blarg': 'wibble'}
        body = {'not the environment': env}
        data = stacks.InstantiationData(body)
        self.assertEqual({'parameters': {}, 'encrypted_param_names': [],
                          'parameter_defaults': {}, 'resource_registry': {}},
                         data.environment())

    def test_args(self):
        body = {
            'parameters': {},
            'environment': {},
            'stack_name': 'foo',
            'template': {},
            'template_url': 'http://example.com/',
            'timeout_mins': 60,
        }
        data = stacks.InstantiationData(body)
        self.assertEqual({'timeout_mins': 60}, data.args())


@mock.patch.object(policy.Enforcer, 'enforce')
class StackControllerTest(tools.ControllerTest, common.HeatTestCase):
    '''
    Tests the API class which acts as the WSGI controller,
    the endpoint processing API requests after they are routed
    '''

    def setUp(self):
        super(StackControllerTest, self).setUp()
        # Create WSGI controller instance

        class DummyConfig(object):
            bind_port = 8004

        cfgopts = DummyConfig()
        self.controller = stacks.StackController(options=cfgopts)

    @mock.patch.object(rpc_client.EngineClient, 'call')
    def test_index(self, mock_call, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        req = self._get('/stacks')

        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')

        engine_resp = [
            {
                u'stack_identity': dict(identity),
                u'updated_time': u'2012-07-09T09:13:11Z',
                u'template_description': u'blah',
                u'description': u'blah',
                u'stack_status_reason': u'Stack successfully created',
                u'creation_time': u'2012-07-09T09:12:45Z',
                u'stack_name': identity.stack_name,
                u'stack_action': u'CREATE',
                u'stack_status': u'COMPLETE',
                u'parameters': {},
                u'outputs': [],
                u'notification_topics': [],
                u'capabilities': [],
                u'disable_rollback': True,
                u'timeout_mins': 60,
            }
        ]
        mock_call.return_value = engine_resp

        result = self.controller.index(req, tenant_id=identity.tenant)

        expected = {
            'stacks': [
                {
                    'links': [{"href": self._url(identity),
                               "rel": "self"}],
                    'id': '1',
                    u'updated_time': u'2012-07-09T09:13:11Z',
                    u'description': u'blah',
                    u'stack_status_reason': u'Stack successfully created',
                    u'creation_time': u'2012-07-09T09:12:45Z',
                    u'stack_name': u'wordpress',
                    u'stack_status': u'CREATE_COMPLETE'
                }
            ]
        }
        self.assertEqual(expected, result)
        default_args = {'limit': None, 'sort_keys': None, 'marker': None,
                        'sort_dir': None, 'filters': None, 'tenant_safe': True,
                        'show_deleted': False, 'show_nested': False,
                        'show_hidden': False, 'tags': None,
                        'tags_any': None, 'not_tags': None,
                        'not_tags_any': None}
        mock_call.assert_called_once_with(
            req.context, ('list_stacks', default_args), version='1.8')

    @mock.patch.object(rpc_client.EngineClient, 'call')
    def test_index_whitelists_pagination_params(self, mock_call, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        params = {
            'limit': 10,
            'sort_keys': 'fake sort keys',
            'marker': 'fake marker',
            'sort_dir': 'fake sort dir',
            'balrog': 'you shall not pass!'
        }
        req = self._get('/stacks', params=params)
        mock_call.return_value = []

        self.controller.index(req, tenant_id=self.tenant)

        rpc_call_args, _ = mock_call.call_args
        engine_args = rpc_call_args[1][1]
        self.assertEqual(13, len(engine_args))
        self.assertIn('limit', engine_args)
        self.assertIn('sort_keys', engine_args)
        self.assertIn('marker', engine_args)
        self.assertIn('sort_dir', engine_args)
        self.assertIn('filters', engine_args)
        self.assertIn('tenant_safe', engine_args)
        self.assertNotIn('balrog', engine_args)

    @mock.patch.object(rpc_client.EngineClient, 'call')
    def test_index_limit_not_int(self, mock_call, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        params = {'limit': 'not-an-int'}
        req = self._get('/stacks', params=params)

        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.index, req,
                               tenant_id=self.tenant)
        self.assertEqual("Only integer is acceptable by 'limit'.",
                         six.text_type(ex))
        self.assertFalse(mock_call.called)

    @mock.patch.object(rpc_client.EngineClient, 'call')
    def test_index_whitelist_filter_params(self, mock_call, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        params = {
            'id': 'fake id',
            'status': 'fake status',
            'name': 'fake name',
            'action': 'fake action',
            'username': 'fake username',
            'tenant': 'fake tenant',
            'owner_id': 'fake owner-id',
            'balrog': 'you shall not pass!'
        }
        req = self._get('/stacks', params=params)
        mock_call.return_value = []

        self.controller.index(req, tenant_id=self.tenant)

        rpc_call_args, _ = mock_call.call_args
        engine_args = rpc_call_args[1][1]
        self.assertIn('filters', engine_args)

        filters = engine_args['filters']
        self.assertEqual(7, len(filters))
        self.assertIn('id', filters)
        self.assertIn('status', filters)
        self.assertIn('name', filters)
        self.assertIn('action', filters)
        self.assertIn('username', filters)
        self.assertIn('tenant', filters)
        self.assertIn('owner_id', filters)
        self.assertNotIn('balrog', filters)

    def test_index_returns_stack_count_if_with_count_is_true(
            self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        params = {'with_count': 'True'}
        req = self._get('/stacks', params=params)
        engine = self.controller.rpc_client

        engine.list_stacks = mock.Mock(return_value=[])
        engine.count_stacks = mock.Mock(return_value=0)

        result = self.controller.index(req, tenant_id=self.tenant)
        self.assertEqual(0, result['count'])

    def test_index_doesnt_return_stack_count_if_with_count_is_false(
            self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        params = {'with_count': 'false'}
        req = self._get('/stacks', params=params)
        engine = self.controller.rpc_client

        engine.list_stacks = mock.Mock(return_value=[])
        engine.count_stacks = mock.Mock()

        result = self.controller.index(req, tenant_id=self.tenant)
        self.assertNotIn('count', result)
        assert not engine.count_stacks.called

    def test_index_with_count_is_invalid(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        params = {'with_count': 'invalid_value'}
        req = self._get('/stacks', params=params)

        exc = self.assertRaises(webob.exc.HTTPBadRequest,
                                self.controller.index,
                                req, tenant_id=self.tenant)
        excepted = ('Unrecognized value "invalid_value" for "with_count", '
                    'acceptable values are: true, false')
        self.assertIn(excepted, six.text_type(exc))

    @mock.patch.object(rpc_client.EngineClient, 'count_stacks')
    def test_index_doesnt_break_with_old_engine(self, mock_count_stacks,
                                                mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        params = {'with_count': 'True'}
        req = self._get('/stacks', params=params)
        engine = self.controller.rpc_client

        engine.list_stacks = mock.Mock(return_value=[])
        mock_count_stacks.side_effect = AttributeError("Should not exist")

        result = self.controller.index(req, tenant_id=self.tenant)
        self.assertNotIn('count', result)

    def test_index_enforces_global_index_if_global_tenant(self, mock_enforce):
        params = {'global_tenant': 'True'}
        req = self._get('/stacks', params=params)
        rpc_client = self.controller.rpc_client

        rpc_client.list_stacks = mock.Mock(return_value=[])
        rpc_client.count_stacks = mock.Mock()

        self.controller.index(req, tenant_id=self.tenant)
        mock_enforce.assert_called_with(action='global_index',
                                        scope=self.controller.REQUEST_SCOPE,
                                        context=self.context)

    def test_global_index_sets_tenant_safe_to_false(self, mock_enforce):
        rpc_client = self.controller.rpc_client
        rpc_client.list_stacks = mock.Mock(return_value=[])
        rpc_client.count_stacks = mock.Mock()

        params = {'global_tenant': 'True'}
        req = self._get('/stacks', params=params)
        self.controller.index(req, tenant_id=self.tenant)
        rpc_client.list_stacks.assert_called_once_with(mock.ANY,
                                                       filters=mock.ANY,
                                                       tenant_safe=False)

    def test_global_index_show_deleted_false(self, mock_enforce):
        rpc_client = self.controller.rpc_client
        rpc_client.list_stacks = mock.Mock(return_value=[])
        rpc_client.count_stacks = mock.Mock()

        params = {'show_deleted': 'False'}
        req = self._get('/stacks', params=params)
        self.controller.index(req, tenant_id=self.tenant)
        rpc_client.list_stacks.assert_called_once_with(mock.ANY,
                                                       filters=mock.ANY,
                                                       tenant_safe=True,
                                                       show_deleted=False)

    def test_global_index_show_deleted_true(self, mock_enforce):
        rpc_client = self.controller.rpc_client
        rpc_client.list_stacks = mock.Mock(return_value=[])
        rpc_client.count_stacks = mock.Mock()

        params = {'show_deleted': 'True'}
        req = self._get('/stacks', params=params)
        self.controller.index(req, tenant_id=self.tenant)
        rpc_client.list_stacks.assert_called_once_with(mock.ANY,
                                                       filters=mock.ANY,
                                                       tenant_safe=True,
                                                       show_deleted=True)

    def test_global_index_show_nested_false(self, mock_enforce):
        rpc_client = self.controller.rpc_client
        rpc_client.list_stacks = mock.Mock(return_value=[])
        rpc_client.count_stacks = mock.Mock()

        params = {'show_nested': 'False'}
        req = self._get('/stacks', params=params)
        self.controller.index(req, tenant_id=self.tenant)
        rpc_client.list_stacks.assert_called_once_with(mock.ANY,
                                                       filters=mock.ANY,
                                                       tenant_safe=True,
                                                       show_nested=False)

    def test_global_index_show_nested_true(self, mock_enforce):
        rpc_client = self.controller.rpc_client
        rpc_client.list_stacks = mock.Mock(return_value=[])
        rpc_client.count_stacks = mock.Mock()

        params = {'show_nested': 'True'}
        req = self._get('/stacks', params=params)
        self.controller.index(req, tenant_id=self.tenant)
        rpc_client.list_stacks.assert_called_once_with(mock.ANY,
                                                       filters=mock.ANY,
                                                       tenant_safe=True,
                                                       show_nested=True)

    def test_index_show_deleted_True_with_count_True(self, mock_enforce):
        rpc_client = self.controller.rpc_client
        rpc_client.list_stacks = mock.Mock(return_value=[])
        rpc_client.count_stacks = mock.Mock(return_value=0)

        params = {'show_deleted': 'True',
                  'with_count': 'True'}
        req = self._get('/stacks', params=params)
        result = self.controller.index(req, tenant_id=self.tenant)
        self.assertEqual(0, result['count'])
        rpc_client.list_stacks.assert_called_once_with(mock.ANY,
                                                       filters=mock.ANY,
                                                       tenant_safe=True,
                                                       show_deleted=True)
        rpc_client.count_stacks.assert_called_once_with(mock.ANY,
                                                        filters=mock.ANY,
                                                        tenant_safe=True,
                                                        show_deleted=True,
                                                        show_nested=False,
                                                        show_hidden=False,
                                                        tags=None,
                                                        tags_any=None,
                                                        not_tags=None,
                                                        not_tags_any=None)

    @mock.patch.object(rpc_client.EngineClient, 'call')
    def test_detail(self, mock_call, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'detail', True)
        req = self._get('/stacks/detail')

        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')

        engine_resp = [
            {
                u'stack_identity': dict(identity),
                u'updated_time': u'2012-07-09T09:13:11Z',
                u'template_description': u'blah',
                u'description': u'blah',
                u'stack_status_reason': u'Stack successfully created',
                u'creation_time': u'2012-07-09T09:12:45Z',
                u'stack_name': identity.stack_name,
                u'stack_action': u'CREATE',
                u'stack_status': u'COMPLETE',
                u'parameters': {'foo': 'bar'},
                u'outputs': ['key', 'value'],
                u'notification_topics': [],
                u'capabilities': [],
                u'disable_rollback': True,
                u'timeout_mins': 60,
            }
        ]
        mock_call.return_value = engine_resp

        result = self.controller.detail(req, tenant_id=identity.tenant)

        expected = {
            'stacks': [
                {
                    'links': [{"href": self._url(identity),
                               "rel": "self"}],
                    'id': '1',
                    u'updated_time': u'2012-07-09T09:13:11Z',
                    u'template_description': u'blah',
                    u'description': u'blah',
                    u'stack_status_reason': u'Stack successfully created',
                    u'creation_time': u'2012-07-09T09:12:45Z',
                    u'stack_name': identity.stack_name,
                    u'stack_status': u'CREATE_COMPLETE',
                    u'parameters': {'foo': 'bar'},
                    u'outputs': ['key', 'value'],
                    u'notification_topics': [],
                    u'capabilities': [],
                    u'disable_rollback': True,
                    u'timeout_mins': 60,
                }
            ]
        }

        self.assertEqual(expected, result)
        default_args = {'limit': None, 'sort_keys': None, 'marker': None,
                        'sort_dir': None, 'filters': None, 'tenant_safe': True,
                        'show_deleted': False, 'show_nested': False,
                        'show_hidden': False, 'tags': None,
                        'tags_any': None, 'not_tags': None,
                        'not_tags_any': None}
        mock_call.assert_called_once_with(
            req.context, ('list_stacks', default_args), version='1.8')

    @mock.patch.object(rpc_client.EngineClient, 'call')
    def test_index_rmt_aterr(self, mock_call, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        req = self._get('/stacks')

        mock_call.side_effect = tools.to_remote_error(AttributeError())

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.index,
                                             req, tenant_id=self.tenant)

        self.assertEqual(400, resp.json['code'])
        self.assertEqual('AttributeError', resp.json['error']['type'])
        mock_call.assert_called_once_with(
            req.context, ('list_stacks', mock.ANY), version='1.8')

    def test_index_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', False)

        req = self._get('/stacks')

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.index,
                                             req, tenant_id=self.tenant)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    @mock.patch.object(rpc_client.EngineClient, 'call')
    def test_index_rmt_interr(self, mock_call, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'index', True)
        req = self._get('/stacks')

        mock_call.side_effect = tools.to_remote_error(Exception())

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.index,
                                             req, tenant_id=self.tenant)

        self.assertEqual(500, resp.json['code'])
        self.assertEqual('Exception', resp.json['error']['type'])
        mock_call.assert_called_once_with(
            req.context, ('list_stacks', mock.ANY), version='1.8')

    def test_create(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'stack_name': identity.stack_name,
                'parameters': parameters,
                'timeout_mins': 30}

        req = self._post('/stacks', json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': identity.stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        response = self.controller.create(req,
                                          tenant_id=identity.tenant,
                                          body=body)

        expected = {'stack':
                    {'id': '1',
                     'links': [{'href': self._url(identity), 'rel': 'self'}]}}
        self.assertEqual(expected, response)

        self.m.VerifyAll()

    def test_create_with_tags(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'stack_name': identity.stack_name,
                'parameters': parameters,
                'tags': 'tag1,tag2',
                'timeout_mins': 30}

        req = self._post('/stacks', json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': identity.stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30, 'tags': ['tag1', 'tag2']},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        response = self.controller.create(req,
                                          tenant_id=identity.tenant,
                                          body=body)

        expected = {'stack':
                    {'id': '1',
                     'links': [{'href': self._url(identity), 'rel': 'self'}]}}
        self.assertEqual(expected, response)
        self.m.VerifyAll()

    def test_adopt(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')
        template = {
            "heat_template_version": "2013-05-23",
            "parameters": {"app_dbx": {"type": "string"}},
            "resources": {"res1": {"type": "GenericResourceType"}}}

        parameters = {"app_dbx": "test"}
        adopt_data = {
            "status": "COMPLETE",
            "name": "rtrove1",
            "parameters": parameters,
            "template": template,
            "action": "CREATE",
            "id": "8532f0d3-ea84-444e-b2bb-2543bb1496a4",
            "resources": {"res1": {
                    "status": "COMPLETE",
                    "name": "database_password",
                    "resource_id": "yBpuUROjfGQ2gKOD",
                    "action": "CREATE",
                    "type": "GenericResourceType",
                    "metadata": {}}}}
        body = {'template': None,
                'stack_name': identity.stack_name,
                'parameters': parameters,
                'timeout_mins': 30,
                'adopt_stack_data': str(adopt_data)}

        req = self._post('/stacks', json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': identity.stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30,
                       'adopt_stack_data': str(adopt_data)},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        response = self.controller.create(req,
                                          tenant_id=identity.tenant,
                                          body=body)

        expected = {'stack':
                    {'id': '1',
                     'links': [{'href': self._url(identity), 'rel': 'self'}]}}
        self.assertEqual(expected, response)
        self.m.VerifyAll()

    def test_adopt_timeout_not_int(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')

        body = {'template': None,
                'stack_name': identity.stack_name,
                'parameters': {},
                'timeout_mins': 'not-an-int',
                'adopt_stack_data': 'does not matter'}

        req = self._post('/stacks', json.dumps(body))

        mock_call = self.patchobject(rpc_client.EngineClient, 'call')
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.create, req,
                               tenant_id=self.tenant, body=body)

        self.assertEqual("Only integer is acceptable by 'timeout_mins'.",
                         six.text_type(ex))
        self.assertFalse(mock_call.called)

    def test_adopt_error(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')
        parameters = {"app_dbx": "test"}
        adopt_data = ["Test"]
        body = {'template': None,
                'stack_name': identity.stack_name,
                'parameters': parameters,
                'timeout_mins': 30,
                'adopt_stack_data': str(adopt_data)}

        req = self._post('/stacks', json.dumps(body))

        self.m.ReplayAll()
        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.create,
                                             req, tenant_id=self.tenant,
                                             body=body)
        self.assertEqual(400, resp.status_code)
        self.assertEqual('400 Bad Request', resp.status)
        self.assertIn('Invalid adopt data', resp.text)
        self.m.VerifyAll()

    def test_create_with_files(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'stack_name': identity.stack_name,
                'parameters': parameters,
                'files': {'my.yaml': 'This is the file contents.'},
                'timeout_mins': 30}

        req = self._post('/stacks', json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': identity.stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {'my.yaml': 'This is the file contents.'},
              'args': {'timeout_mins': 30},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        result = self.controller.create(req,
                                        tenant_id=identity.tenant,
                                        body=body)
        expected = {'stack':
                    {'id': '1',
                     'links': [{'href': self._url(identity), 'rel': 'self'}]}}
        self.assertEqual(expected, result)

        self.m.VerifyAll()

    def test_create_err_rpcerr(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True, 3)
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'stack_name': stack_name,
                'parameters': parameters,
                'timeout_mins': 30}

        req = self._post('/stacks', json.dumps(body))

        unknown_parameter = heat_exc.UnknownUserParameter(key='a')
        missing_parameter = heat_exc.UserParameterMissing(key='a')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndRaise(tools.to_remote_error(AttributeError()))
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndRaise(tools.to_remote_error(unknown_parameter))
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndRaise(tools.to_remote_error(missing_parameter))
        self.m.ReplayAll()
        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.create,
                                             req, tenant_id=self.tenant,
                                             body=body)

        self.assertEqual(400, resp.json['code'])
        self.assertEqual('AttributeError', resp.json['error']['type'])

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.create,
                                             req, tenant_id=self.tenant,
                                             body=body)

        self.assertEqual(400, resp.json['code'])
        self.assertEqual('UnknownUserParameter', resp.json['error']['type'])

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.create,
                                             req, tenant_id=self.tenant,
                                             body=body)

        self.assertEqual(400, resp.json['code'])
        self.assertEqual('UserParameterMissing', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_create_err_existing(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'stack_name': stack_name,
                'parameters': parameters,
                'timeout_mins': 30}

        req = self._post('/stacks', json.dumps(body))

        error = heat_exc.StackExists(stack_name='s')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.create,
                                             req, tenant_id=self.tenant,
                                             body=body)

        self.assertEqual(409, resp.json['code'])
        self.assertEqual('StackExists', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_create_timeout_not_int(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'stack_name': stack_name,
                'parameters': parameters,
                'timeout_mins': 'not-an-int'}

        req = self._post('/stacks', json.dumps(body))

        mock_call = self.patchobject(rpc_client.EngineClient, 'call')
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.create, req,
                               tenant_id=self.tenant, body=body)

        self.assertEqual("Only integer is acceptable by 'timeout_mins'.",
                         six.text_type(ex))
        self.assertFalse(mock_call.called)

    def test_create_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', False)
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'stack_name': stack_name,
                'parameters': parameters,
                'timeout_mins': 30}

        req = self._post('/stacks', json.dumps(body))

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.create,
                                             req, tenant_id=self.tenant,
                                             body=body)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_create_err_engine(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'create', True)
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'stack_name': stack_name,
                'parameters': parameters,
                'timeout_mins': 30}

        req = self._post('/stacks', json.dumps(body))

        error = heat_exc.StackValidationFailed(message='')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('create_stack',
             {'stack_name': stack_name,
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30},
              'owner_id': None,
              'nested_depth': 0,
              'user_creds_id': None,
              'parent_resource_name': None,
              'stack_user_project_id': None}),
            version='1.8'
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.create,
                                             req, tenant_id=self.tenant,
                                             body=body)

        self.assertEqual(400, resp.json['code'])
        self.assertEqual('StackValidationFailed', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_create_err_stack_bad_reqest(self, mock_enforce):
        cfg.CONF.set_override('debug', True)
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'timeout_mins': 30}

        req = self._post('/stacks', json.dumps(body))

        error = heat_exc.HTTPExceptionDisguise(webob.exc.HTTPBadRequest())
        self.controller.create = mock.MagicMock(side_effect=error)

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.create, req, body)

        # When HTTP disguised exceptions reach the fault app, they are
        # converted into regular responses, just like non-HTTP exceptions
        self.assertEqual(400, resp.json['code'])
        self.assertEqual('HTTPBadRequest', resp.json['error']['type'])
        self.assertIsNotNone(resp.json['error']['traceback'])

    @mock.patch.object(rpc_client.EngineClient, 'call')
    @mock.patch.object(stacks.stacks_view, 'format_stack')
    def test_preview_stack(self, mock_format, mock_call, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'preview', True)
        body = {'stack_name': 'foo', 'template': {}}
        req = self._get('/stacks/preview', params={})
        mock_call.return_value = {}
        mock_format.return_value = 'formatted_stack'

        result = self.controller.preview(req, tenant_id=self.tenant, body=body)

        self.assertEqual({'stack': 'formatted_stack'}, result)

    def test_preview_update_stack(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'preview_update', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'files': {},
                'timeout_mins': 30}

        req = self._put('/stacks/%(stack_name)s/%(stack_id)s/preview' %
                        identity, json.dumps(body))
        resource_changes = {'updated': [],
                            'deleted': [],
                            'unchanged': [],
                            'added': [],
                            'replaced': []}

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('preview_update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30}}),
            version='1.15'
        ).AndReturn(resource_changes)
        self.m.ReplayAll()

        result = self.controller.preview_update(req, tenant_id=identity.tenant,
                                                stack_name=identity.stack_name,
                                                stack_id=identity.stack_id,
                                                body=body)
        self.assertEqual({'resource_changes': resource_changes}, result)
        self.m.VerifyAll()

    def test_lookup(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'lookup', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')

        req = self._get('/stacks/%(stack_name)s' % identity)

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('identify_stack', {'stack_name': identity.stack_name})
        ).AndReturn(identity)

        self.m.ReplayAll()

        found = self.assertRaises(
            webob.exc.HTTPFound, self.controller.lookup, req,
            tenant_id=identity.tenant, stack_name=identity.stack_name)
        self.assertEqual(self._url(identity), found.location)

        self.m.VerifyAll()

    def test_lookup_arn(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'lookup', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')

        req = self._get('/stacks%s' % identity.arn_url_path())

        self.m.ReplayAll()

        found = self.assertRaises(
            webob.exc.HTTPFound, self.controller.lookup,
            req, tenant_id=identity.tenant, stack_name=identity.arn())
        self.assertEqual(self._url(identity), found.location)

        self.m.VerifyAll()

    def test_lookup_nonexistent(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'lookup', True)
        stack_name = 'wibble'

        req = self._get('/stacks/%(stack_name)s' % {
            'stack_name': stack_name})

        error = heat_exc.StackNotFound(stack_name='a')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('identify_stack', {'stack_name': stack_name})
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.lookup,
                                             req, tenant_id=self.tenant,
                                             stack_name=stack_name)

        self.assertEqual(404, resp.json['code'])
        self.assertEqual('StackNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_lookup_err_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'lookup', False)
        stack_name = 'wibble'

        req = self._get('/stacks/%(stack_name)s' % {
            'stack_name': stack_name})

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.lookup,
                                             req, tenant_id=self.tenant,
                                             stack_name=stack_name)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_lookup_resource(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'lookup', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '1')

        req = self._get('/stacks/%(stack_name)s/resources' % identity)

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('identify_stack', {'stack_name': identity.stack_name})
        ).AndReturn(identity)

        self.m.ReplayAll()

        found = self.assertRaises(
            webob.exc.HTTPFound, self.controller.lookup, req,
            tenant_id=identity.tenant, stack_name=identity.stack_name,
            path='resources')
        self.assertEqual(self._url(identity) + '/resources',
                         found.location)

        self.m.VerifyAll()

    def test_lookup_resource_nonexistent(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'lookup', True)
        stack_name = 'wibble'

        req = self._get('/stacks/%(stack_name)s/resources' % {
            'stack_name': stack_name})

        error = heat_exc.StackNotFound(stack_name='a')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('identify_stack', {'stack_name': stack_name})
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.lookup,
                                             req, tenant_id=self.tenant,
                                             stack_name=stack_name,
                                             path='resources')

        self.assertEqual(404, resp.json['code'])
        self.assertEqual('StackNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_lookup_resource_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'lookup', False)
        stack_name = 'wibble'

        req = self._get('/stacks/%(stack_name)s/resources' % {
            'stack_name': stack_name})

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.lookup,
                                             req, tenant_id=self.tenant,
                                             stack_name=stack_name,
                                             path='resources')

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_show(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'show', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')

        req = self._get('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        parameters = {u'DBUsername': u'admin',
                      u'LinuxDistribution': u'F17',
                      u'InstanceType': u'm1.large',
                      u'DBRootPassword': u'admin',
                      u'DBPassword': u'admin',
                      u'DBName': u'wordpress'}
        outputs = [{u'output_key': u'WebsiteURL',
                    u'description': u'URL for Wordpress wiki',
                    u'output_value': u'http://10.0.0.8/wordpress'}]

        engine_resp = [
            {
                u'stack_identity': dict(identity),
                u'updated_time': u'2012-07-09T09:13:11Z',
                u'parameters': parameters,
                u'outputs': outputs,
                u'stack_status_reason': u'Stack successfully created',
                u'creation_time': u'2012-07-09T09:12:45Z',
                u'stack_name': identity.stack_name,
                u'notification_topics': [],
                u'stack_action': u'CREATE',
                u'stack_status': u'COMPLETE',
                u'description': u'blah',
                u'disable_rollback': True,
                u'timeout_mins':60,
                u'capabilities': [],
            }
        ]
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('show_stack', {'stack_identity': dict(identity)})
        ).AndReturn(engine_resp)
        self.m.ReplayAll()

        response = self.controller.show(req,
                                        tenant_id=identity.tenant,
                                        stack_name=identity.stack_name,
                                        stack_id=identity.stack_id)

        expected = {
            'stack': {
                'links': [{"href": self._url(identity),
                           "rel": "self"}],
                'id': '6',
                u'updated_time': u'2012-07-09T09:13:11Z',
                u'parameters': parameters,
                u'outputs': outputs,
                u'description': u'blah',
                u'stack_status_reason': u'Stack successfully created',
                u'creation_time': u'2012-07-09T09:12:45Z',
                u'stack_name': identity.stack_name,
                u'stack_status': u'CREATE_COMPLETE',
                u'capabilities': [],
                u'notification_topics': [],
                u'disable_rollback': True,
                u'timeout_mins': 60,
            }
        }
        self.assertEqual(expected, response)
        self.m.VerifyAll()

    def test_show_notfound(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'show', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wibble', '6')

        req = self._get('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        error = heat_exc.StackNotFound(stack_name='a')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('show_stack', {'stack_identity': dict(identity)})
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.show,
                                             req, tenant_id=identity.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id)

        self.assertEqual(404, resp.json['code'])
        self.assertEqual('StackNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_show_invalidtenant(self, mock_enforce):
        identity = identifier.HeatIdentifier('wibble', 'wordpress', '6')

        req = self._get('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.show,
                                             req, tenant_id=identity.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))
        self.m.VerifyAll()

    def test_show_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'show', False)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')

        req = self._get('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.show,
                                             req, tenant_id=identity.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_get_template(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'template', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        req = self._get('/stacks/%(stack_name)s/%(stack_id)s' % identity)
        template = {u'Foo': u'bar'}

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('get_template', {'stack_identity': dict(identity)})
        ).AndReturn(template)
        self.m.ReplayAll()

        response = self.controller.template(req, tenant_id=identity.tenant,
                                            stack_name=identity.stack_name,
                                            stack_id=identity.stack_id)

        self.assertEqual(template, response)
        self.m.VerifyAll()

    def test_get_template_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'template', False)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        req = self._get('/stacks/%(stack_name)s/%(stack_id)s/template'
                        % identity)

        self.m.ReplayAll()
        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.template,
                                             req, tenant_id=identity.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))
        self.m.VerifyAll()

    def test_get_template_err_notfound(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'template', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        req = self._get('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        error = heat_exc.StackNotFound(stack_name='a')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('get_template', {'stack_identity': dict(identity)})
        ).AndRaise(tools.to_remote_error(error))

        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.template,
                                             req, tenant_id=identity.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id)

        self.assertEqual(404, resp.json['code'])
        self.assertEqual('StackNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_update(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'files': {},
                'timeout_mins': 30}

        req = self._put('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                        json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30}})
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPAccepted,
                          self.controller.update,
                          req, tenant_id=identity.tenant,
                          stack_name=identity.stack_name,
                          stack_id=identity.stack_id,
                          body=body)
        self.m.VerifyAll()

    def test_update_with_tags(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'files': {},
                'tags': 'tag1,tag2',
                'timeout_mins': 30}

        req = self._put('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                        json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30, 'tags': ['tag1', 'tag2']}})
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPAccepted,
                          self.controller.update,
                          req, tenant_id=identity.tenant,
                          stack_name=identity.stack_name,
                          stack_id=identity.stack_id,
                          body=body)
        self.m.VerifyAll()

    def test_update_bad_name(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wibble', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'files': {},
                'timeout_mins': 30}

        req = self._put('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                        json.dumps(body))

        error = heat_exc.StackNotFound(stack_name='a')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {u'parameters': parameters,
                         u'encrypted_param_names': [],
                         u'parameter_defaults': {},
                         u'resource_registry': {}},
              'files': {},
              'args': {'timeout_mins': 30}})
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.update,
                                             req, tenant_id=identity.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id,
                                             body=body)

        self.assertEqual(404, resp.json['code'])
        self.assertEqual('StackNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_update_timeout_not_int(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wibble', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'files': {},
                'timeout_mins': 'not-int'}

        req = self._put('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                        json.dumps(body))

        mock_call = self.patchobject(rpc_client.EngineClient, 'call')
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.update, req,
                               tenant_id=identity.tenant,
                               stack_name=identity.stack_name,
                               stack_id=identity.stack_id,
                               body=body)
        self.assertEqual("Only integer is acceptable by 'timeout_mins'.",
                         six.text_type(ex))
        self.assertFalse(mock_call.called)

    def test_update_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update', False)
        identity = identifier.HeatIdentifier(self.tenant, 'wibble', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'files': {},
                'timeout_mins': 30}

        req = self._put('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                        json.dumps(body))

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.update,
                                             req, tenant_id=identity.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id,
                                             body=body)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_update_with_existing_parameters(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update_patch', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        body = {'template': template,
                'parameters': {},
                'files': {},
                'timeout_mins': 30}

        req = self._patch('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                          json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {'parameters': {},
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {rpc_api.PARAM_EXISTING: True,
                       'timeout_mins': 30}})
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPAccepted,
                          self.controller.update_patch,
                          req, tenant_id=identity.tenant,
                          stack_name=identity.stack_name,
                          stack_id=identity.stack_id,
                          body=body)
        self.m.VerifyAll()

    def test_update_with_existing_parameters_with_tags(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update_patch', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        body = {'template': template,
                'parameters': {},
                'files': {},
                'tags': 'tag1,tag2',
                'timeout_mins': 30}

        req = self._patch('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                          json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {'parameters': {},
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {rpc_api.PARAM_EXISTING: True,
                       'timeout_mins': 30,
                       'tags': ['tag1', 'tag2']}})
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPAccepted,
                          self.controller.update_patch,
                          req, tenant_id=identity.tenant,
                          stack_name=identity.stack_name,
                          stack_id=identity.stack_id,
                          body=body)
        self.m.VerifyAll()

    def test_update_with_patched_existing_parameters(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update_patch', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'files': {},
                'timeout_mins': 30}

        req = self._patch('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                          json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {rpc_api.PARAM_EXISTING: True,
                       'timeout_mins': 30}})
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPAccepted,
                          self.controller.update_patch,
                          req, tenant_id=identity.tenant,
                          stack_name=identity.stack_name,
                          stack_id=identity.stack_id,
                          body=body)
        self.m.VerifyAll()

    def test_update_with_patch_timeout_not_int(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update_patch', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        body = {'template': template,
                'parameters': parameters,
                'files': {},
                'timeout_mins': 'not-int'}

        req = self._patch('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                          json.dumps(body))

        mock_call = self.patchobject(rpc_client.EngineClient, 'call')
        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.update_patch, req,
                               tenant_id=identity.tenant,
                               stack_name=identity.stack_name,
                               stack_id=identity.stack_id,
                               body=body)
        self.assertEqual("Only integer is acceptable by 'timeout_mins'.",
                         six.text_type(ex))
        self.assertFalse(mock_call.called)

    def test_update_with_existing_and_default_parameters(
            self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update_patch', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        clear_params = [u'DBUsername', u'DBPassword', u'LinuxDistribution']
        body = {'template': template,
                'parameters': {},
                'clear_parameters': clear_params,
                'files': {},
                'timeout_mins': 30}

        req = self._patch('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                          json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {'parameters': {},
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {rpc_api.PARAM_EXISTING: True,
                       'clear_parameters': clear_params,
                       'timeout_mins': 30}})
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPAccepted,
                          self.controller.update_patch,
                          req, tenant_id=identity.tenant,
                          stack_name=identity.stack_name,
                          stack_id=identity.stack_id,
                          body=body)
        self.m.VerifyAll()

    def test_update_with_patched_and_default_parameters(
            self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'update_patch', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        template = {u'Foo': u'bar'}
        parameters = {u'InstanceType': u'm1.xlarge'}
        clear_params = [u'DBUsername', u'DBPassword', u'LinuxDistribution']
        body = {'template': template,
                'parameters': parameters,
                'clear_parameters': clear_params,
                'files': {},
                'timeout_mins': 30}

        req = self._patch('/stacks/%(stack_name)s/%(stack_id)s' % identity,
                          json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('update_stack',
             {'stack_identity': dict(identity),
              'template': template,
              'params': {'parameters': parameters,
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}},
              'files': {},
              'args': {rpc_api.PARAM_EXISTING: True,
                       'clear_parameters': clear_params,
                       'timeout_mins': 30}})
        ).AndReturn(dict(identity))
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPAccepted,
                          self.controller.update_patch,
                          req, tenant_id=identity.tenant,
                          stack_name=identity.stack_name,
                          stack_id=identity.stack_id,
                          body=body)
        self.m.VerifyAll()

    def test_delete(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'delete', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')

        req = self._delete('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        # Engine returns None when delete successful
        rpc_client.EngineClient.call(
            req.context,
            ('delete_stack', {'stack_identity': dict(identity)})
        ).AndReturn(None)
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPNoContent,
                          self.controller.delete,
                          req, tenant_id=identity.tenant,
                          stack_name=identity.stack_name,
                          stack_id=identity.stack_id)
        self.m.VerifyAll()

    def test_delete_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'delete', False)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')

        req = self._delete('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.delete,
                                             req, tenant_id=self.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_abandon(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'abandon', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')
        req = self._abandon('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        # Engine returns json data on abandon completion
        expected = {"name": "test", "id": "123"}
        rpc_client.EngineClient.call(
            req.context,
            ('abandon_stack', {'stack_identity': dict(identity)})
        ).AndReturn(expected)
        self.m.ReplayAll()

        ret = self.controller.abandon(req,
                                      tenant_id=identity.tenant,
                                      stack_name=identity.stack_name,
                                      stack_id=identity.stack_id)
        self.assertEqual(expected, ret)
        self.m.VerifyAll()

    def test_abandon_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'abandon', False)
        identity = identifier.HeatIdentifier(self.tenant, 'wordpress', '6')

        req = self._abandon('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.abandon,
                                             req, tenant_id=self.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_delete_bad_name(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'delete', True)
        identity = identifier.HeatIdentifier(self.tenant, 'wibble', '6')

        req = self._delete('/stacks/%(stack_name)s/%(stack_id)s' % identity)

        error = heat_exc.StackNotFound(stack_name='a')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        # Engine returns None when delete successful
        rpc_client.EngineClient.call(
            req.context,
            ('delete_stack', {'stack_identity': dict(identity)})
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.delete,
                                             req, tenant_id=identity.tenant,
                                             stack_name=identity.stack_name,
                                             stack_id=identity.stack_id)

        self.assertEqual(404, resp.json['code'])
        self.assertEqual('StackNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_validate_template(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'validate_template', True)
        template = {u'Foo': u'bar'}
        body = {'template': template}

        req = self._post('/validate', json.dumps(body))

        engine_response = {
            u'Description': u'blah',
            u'Parameters': [
                {
                    u'NoEcho': u'false',
                    u'ParameterKey': u'InstanceType',
                    u'Description': u'Instance type'
                }
            ]
        }

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('validate_template',
             {'template': template,
              'params': {'parameters': {},
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}}})
        ).AndReturn(engine_response)
        self.m.ReplayAll()

        response = self.controller.validate_template(req,
                                                     tenant_id=self.tenant,
                                                     body=body)
        self.assertEqual(engine_response, response)
        self.m.VerifyAll()

    def test_validate_template_error(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'validate_template', True)
        template = {u'Foo': u'bar'}
        body = {'template': template}

        req = self._post('/validate', json.dumps(body))

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('validate_template',
             {'template': template,
              'params': {'parameters': {},
                         'encrypted_param_names': [],
                         'parameter_defaults': {},
                         'resource_registry': {}}})
        ).AndReturn({'Error': 'fubar'})
        self.m.ReplayAll()

        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.controller.validate_template,
                          req, tenant_id=self.tenant, body=body)
        self.m.VerifyAll()

    def test_validate_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'validate_template', False)
        template = {u'Foo': u'bar'}
        body = {'template': template}

        req = self._post('/validate', json.dumps(body))

        resp = tools.request_with_middleware(
            fault.FaultWrapper,
            self.controller.validate_template,
            req, tenant_id=self.tenant, body=body)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_list_resource_types(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'list_resource_types', True)
        req = self._get('/resource_types')

        engine_response = ['AWS::EC2::Instance',
                           'AWS::EC2::EIP',
                           'AWS::EC2::EIPAssociation']

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context, ('list_resource_types', {'support_status': None}),
            version="1.1"
        ).AndReturn(engine_response)
        self.m.ReplayAll()
        response = self.controller.list_resource_types(req,
                                                       tenant_id=self.tenant)
        self.assertEqual({'resource_types': engine_response}, response)
        self.m.VerifyAll()

    def test_list_resource_types_error(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'list_resource_types', True)
        req = self._get('/resource_types')

        error = heat_exc.ResourceTypeNotFound(type_name='')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('list_resource_types',
             {'support_status': None},
             ), version="1.1"
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(
            fault.FaultWrapper,
            self.controller.list_resource_types,
            req, tenant_id=self.tenant)

        self.assertEqual(404, resp.json['code'])
        self.assertEqual('ResourceTypeNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_list_resource_types_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'list_resource_types', False)
        req = self._get('/resource_types')
        resp = tools.request_with_middleware(
            fault.FaultWrapper,
            self.controller.list_resource_types,
            req, tenant_id=self.tenant)

        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_list_template_versions(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'list_template_versions', True)
        req = self._get('/template_versions')

        engine_response = [
            {'version': 'heat_template_version.2013-05-23', 'type': 'hot'},
            {'version': 'AWSTemplateFormatVersion.2010-09-09', 'type': 'cfn'}]

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context, ('list_template_versions', {}),
            version="1.11"
        ).AndReturn(engine_response)
        self.m.ReplayAll()
        response = self.controller.list_template_versions(
            req, tenant_id=self.tenant)
        self.assertEqual({'template_versions': engine_response}, response)
        self.m.VerifyAll()

    def test_list_template_functions(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'list_template_functions', True)
        req = self._get('/template_versions/t1/functions')

        engine_response = [
            {'functions': 'func1', 'description': 'desc1'},
        ]

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context, (
                'list_template_functions', {'template_version': 't1'}),
            version="1.13"
        ).AndReturn(engine_response)
        self.m.ReplayAll()
        response = self.controller.list_template_functions(
            req, tenant_id=self.tenant, template_version='t1')
        self.assertEqual({'template_functions': engine_response}, response)
        self.m.VerifyAll()

    def test_resource_schema(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'resource_schema', True)
        req = self._get('/resource_types/ResourceWithProps')
        type_name = 'ResourceWithProps'

        engine_response = {
            'resource_type': type_name,
            'properties': {
                'Foo': {'type': 'string', 'required': False},
            },
            'attributes': {
                'foo': {'description': 'A generic attribute'},
                'Foo': {'description': 'Another generic attribute'},
            },
            'support_status': {
                'status': 'SUPPORTED',
                'version': None,
                'message': None,
            },
        }
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('resource_schema', {'type_name': type_name})
        ).AndReturn(engine_response)
        self.m.ReplayAll()
        response = self.controller.resource_schema(req,
                                                   tenant_id=self.tenant,
                                                   type_name=type_name)
        self.assertEqual(engine_response, response)
        self.m.VerifyAll()

    def test_resource_schema_nonexist(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'resource_schema', True)
        req = self._get('/resource_types/BogusResourceType')
        type_name = 'BogusResourceType'

        error = heat_exc.ResourceTypeNotFound(type_name='BogusResourceType')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('resource_schema', {'type_name': type_name})
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.resource_schema,
                                             req, tenant_id=self.tenant,
                                             type_name=type_name)
        self.assertEqual(404, resp.json['code'])
        self.assertEqual('ResourceTypeNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_resource_schema_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'resource_schema', False)
        req = self._get('/resource_types/BogusResourceType')
        type_name = 'BogusResourceType'

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.resource_schema,
                                             req, tenant_id=self.tenant,
                                             type_name=type_name)
        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))

    def test_generate_template(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'generate_template', True)
        req = self._get('/resource_types/TEST_TYPE/template')

        engine_response = {'Type': 'TEST_TYPE'}

        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('generate_template', {'type_name': 'TEST_TYPE',
                                   'template_type': 'cfn'}),
            version='1.9'
        ).AndReturn(engine_response)
        self.m.ReplayAll()
        self.controller.generate_template(req, tenant_id=self.tenant,
                                          type_name='TEST_TYPE')
        self.m.VerifyAll()

    def test_generate_template_invalid_template_type(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'generate_template', True)
        params = {'template_type': 'invalid'}
        mock_call = self.patchobject(rpc_client.EngineClient, 'call')

        req = self._get('/resource_types/TEST_TYPE/template',
                        params=params)

        ex = self.assertRaises(webob.exc.HTTPBadRequest,
                               self.controller.generate_template,
                               req, tenant_id=self.tenant,
                               type_name='TEST_TYPE')
        self.assertIn('Template type is not supported: Invalid template '
                      'type "invalid", valid types are: cfn, hot.',
                      six.text_type(ex))
        self.assertFalse(mock_call.called)

    def test_generate_template_not_found(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'generate_template', True)
        req = self._get('/resource_types/NOT_FOUND/template')

        error = heat_exc.ResourceTypeNotFound(type_name='a')
        self.m.StubOutWithMock(rpc_client.EngineClient, 'call')
        rpc_client.EngineClient.call(
            req.context,
            ('generate_template', {'type_name': 'NOT_FOUND',
                                   'template_type': 'cfn'}),
            version='1.9'
        ).AndRaise(tools.to_remote_error(error))
        self.m.ReplayAll()
        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.generate_template,
                                             req, tenant_id=self.tenant,
                                             type_name='NOT_FOUND')
        self.assertEqual(404, resp.json['code'])
        self.assertEqual('ResourceTypeNotFound', resp.json['error']['type'])
        self.m.VerifyAll()

    def test_generate_template_err_denied_policy(self, mock_enforce):
        self._mock_enforce_setup(mock_enforce, 'generate_template', False)
        req = self._get('/resource_types/NOT_FOUND/template')

        resp = tools.request_with_middleware(fault.FaultWrapper,
                                             self.controller.generate_template,
                                             req, tenant_id=self.tenant,
                                             type_name='blah')
        self.assertEqual(403, resp.status_int)
        self.assertIn('403 Forbidden', six.text_type(resp))


class StackSerializerTest(common.HeatTestCase):

    def setUp(self):
        super(StackSerializerTest, self).setUp()
        self.serializer = stacks.StackSerializer()

    def test_serialize_create(self):
        result = {'stack':
                  {'id': '1',
                   'links': [{'href': 'location', "rel": "self"}]}}
        response = webob.Response()
        response = self.serializer.create(response, result)
        self.assertEqual(201, response.status_int)
        self.assertEqual('location', response.headers['Location'])
        self.assertEqual('application/json', response.headers['Content-Type'])
