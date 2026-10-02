from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock
from device_admin import Device, ROOT
from device_health import Health
from device_model import DEFAULTS
from platform_client import Platform
from device_identity import Identity
from PIL import Image
from live_scoreboard import Feed

MATCH='11111111-1111-4111-8111-111111111111'
SESSION='22222222-2222-4222-8222-222222222222'


class HealthControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)
        self.device=Device(self.path/'config.json','https://example.test',ROOT/'fonts')
        self.device.platform_available=lambda:True
    def tearDown(self): self.tmp.cleanup()
    def desired(self,revision=1): return dict(revision=revision,mode='MATCH',effectiveMatchId=MATCH,state='PENDING')
    def test_default_logo_accepts_first_assignment_but_intentional_blank_does_not(self):
        self.device.accept_control(self.desired(),True)
        self.assertEqual(self.device.effective_settings()['match_id'],MATCH)
        self.device.apply(dict(mode='blank'))
        self.device.accept_control(self.desired(2),True)
        self.assertEqual(self.device.effective_settings()['mode'],'blank')
        self.assertEqual(self.device.control.ack['reason'],'LOCAL_TAKEOVER')
        restarted=Device(self.path/'config.json','https://example.test',ROOT/'fonts')
        self.assertTrue(restarted.control.value['localTakeover'])
        self.device.resume_platform()
        self.device.accept_control(self.desired(3),True)
        self.assertEqual(self.device.effective_settings()['mode'],'match')
    def test_local_manual_survives_remote_switch_and_reboot_needs_fresh_authorization(self):
        self.device.apply(dict(mode='manual'))
        self.device.manual.command(dict(action='start',revision=0))
        self.device.accept_control(self.desired(),True)
        self.assertEqual(self.device.effective_settings()['mode'],'manual')
        self.assertTrue(self.device.manual.snapshot()['running'])
        restarted=Device(self.path/'config.json','https://example.test',ROOT/'fonts')
        self.assertIsNone(restarted.control.desired)
        self.assertFalse(restarted.control.authorized)
        self.device.resume_platform()
        self.assertFalse(self.device.manual.snapshot()['running'])
    def test_brightness_does_not_take_control_or_replace_platform_selection(self):
        self.device.accept_control(self.desired(),True)
        self.device.apply(dict(brightness=.35,timezone='UTC'))
        self.assertFalse(self.device.control.value['localTakeover'])
        self.assertEqual(self.device.effective_settings()['match_id'],MATCH)
        self.assertEqual(self.device.effective_settings()['brightness'],.35)
        self.device.apply(dict(mode='manual'))
        self.device.manual.command(dict(action='start',revision=0))
        self.device.apply(dict(brightness=.25))
        self.assertTrue(self.device.manual.snapshot()['running'])
        self.assertTrue(self.device.control.value['localTakeover'])
    def test_saved_legacy_platform_selection_is_not_overwritten_by_default_cloud_logo(self):
        from device_model import save_config
        save_config(self.path/'config.json',DEFAULTS|dict(mode='match',match_id=MATCH))
        restarted=Device(self.path/'config.json','https://example.test',ROOT/'fonts')
        restarted.platform_available=lambda:True
        restarted.accept_control(dict(revision=0,mode='LOGO',state='NONE'),True)
        self.assertEqual(restarted.effective_settings()['mode'],'logo')
        self.assertEqual(restarted.settings()['match_id'],MATCH)
    def test_frozen_render_loop_stale_even_while_telemetry_sampling_works(self):
        self.device.render(); self.device.frame_at=time.monotonic()-8
        report=self.device.telemetry()
        self.assertEqual(report['rendererState'],'STOPPED')
        self.assertEqual(report['outputState'],'SIMULATOR')
        self.assertGreaterEqual(report['frameAgeMs'],8000)
        self.assertIsNone(report['measuredRefreshHz'])
        self.assertNotIn('physicalVerified',report)
    def test_logo_does_not_report_old_feed_match_and_unsupported_probe_is_null(self):
        self.device.active_id=MATCH
        self.device.health=Health(self.path,root=self.path)
        self.device.render(); report=self.device.telemetry()
        self.assertIsNone(report['selectedMatchId']); self.assertIsNone(report['renderedMatchId'])
        self.assertIsNone(report['cpuTemperatureCelsius']); self.assertIsNone(report['throttled'])
        self.assertNotIn('error',report)
    def test_applied_only_when_actual_target_frame_is_rendered(self):
        self.device.accept_control(self.desired(),True)
        self.device.render()
        self.assertIsNone(self.device.control.ack)
        self.device.feed=Mock(); self.device.feed.view.return_value=None
        self.device.render(); self.assertIsNone(self.device.control.ack)
        self.device.accept_control(dict(revision=2,mode='LOGO',effectiveMatchId=None),True)
        self.device.render(); self.assertEqual(self.device.control.ack['status'],'APPLIED')
        self.device.accept_control(None,False)
        self.assertEqual(self.device.effective_settings()['mode'],'logo')
    def test_fallback_heartbeat_does_not_replay_applied_match_ack(self):
        self.device.accept_control(self.desired(),True)
        self.device.active_id=MATCH
        self.device.publish_frame(Image.new('RGB',(192,64)),'MATCH',MATCH,5,1)
        report=self.device.telemetry()
        self.assertEqual(self.device.control_report(report)['acknowledgement']['status'],'APPLIED')
        self.device.platform_available=lambda:False
        self.assertIsNone(self.device.control_report(self.device.telemetry())['acknowledgement'])
        self.device.platform_available=lambda:True
        self.device.publish_frame(Image.new('RGB',(192,64)),'LOGO',None,None)
        self.assertIsNone(self.device.control_report(self.device.telemetry())['acknowledgement'])
        self.device.publish_frame(Image.new('RGB',(192,64)),'MATCH',MATCH,5,1)
        self.assertEqual(self.device.control_report(self.device.telemetry())['acknowledgement']['status'],'APPLIED')
    def test_schedule_fallback_does_not_acknowledge_schedule_output(self):
        self.device.accept_control(dict(revision=4,mode='SCHEDULE',state='PENDING'),True)
        self.device.publish_frame(Image.new('RGB',(192,64)),'SCHEDULE',None,None,4)
        self.assertIsNotNone(self.device.control_report(self.device.telemetry())['acknowledgement'])
        self.device.publish_frame(Image.new('RGB',(192,64)),'LOGO',None,None)
        self.assertIsNone(self.device.control_report(self.device.telemetry())['acknowledgement'])
    def test_metadata_is_published_with_rendered_frame_not_mutating_feed(self):
        self.device.accept_control(self.desired(),True)
        self.device.feed=Mock(); self.device.feed.view.return_value={'match_id':MATCH,'match_revision':5}
        self.device.renderer.render=Mock(return_value=Image.new('RGB',(192,64)))
        self.device.render()
        self.assertEqual(self.device.rendered_match,MATCH)
        self.assertEqual(self.device.rendered_revision,5)
        self.assertEqual(self.device.control.ack['revision'],1)
        self.device.apply(dict(mode='logo')); self.device.render()
        self.assertIsNone(self.device.rendered_match)
    def test_setup_overlay_publishes_final_mode_without_match_or_acknowledgement(self):
        self.device.accept_control(self.desired(),True)
        self.device.feed=Mock(); self.device.feed.view.return_value={'match_id':MATCH,'match_revision':5}
        self.device.renderer.render=Mock(return_value=Image.new('RGB',(192,64)))
        image,_=self.device.render(publish=False)
        self.assertEqual(self.device.rendered_mode,'UNKNOWN')
        self.assertIsNone(self.device.control.ack)
        self.device.publish_frame(image,'SETUP',None,None,output_success=True)
        report=self.device.telemetry()
        self.assertEqual(report['effectiveMode'],'SETUP')
        self.assertIsNone(report['renderedMatchId']); self.assertIsNone(report['matchRevision'])
        self.assertIsNone(self.device.control.ack)
        self.assertEqual(self.device.output_at,self.device.frame_at)
    def test_real_driver_submission_failure_does_not_acknowledge_switch(self):
        self.device.driver='piomatter'; self.device.output_state='ERROR'
        self.device.accept_control(self.desired(),True)
        image=Image.new('RGB',(192,64))
        self.device.publish_frame(image,'MATCH',MATCH,5,1,output_success=False)
        self.assertIsNone(self.device.control.ack)
        self.device.publish_frame(image,'MATCH',MATCH,5,1,output_success=True)
        self.assertEqual(self.device.control.ack['status'],'APPLIED')
    def test_unsupported_or_malformed_control_never_applies(self):
        self.device.accept_control(dict(revision=1,mode='MATCH',effectiveMatchId='bad-id'),True)
        self.assertEqual(self.device.effective_settings()['mode'],'logo')
        self.assertEqual(self.device.control.ack['reason'],'APPLY_FAILED')
        self.device.accept_control(dict(revision=2,mode='EXECUTE'),True)
        self.assertEqual(self.device.control.ack['reason'],'UNSUPPORTED')
    def test_heartbeat_session_sequence_and_server_control(self):
        identity=Identity(self.path/'identity.json'); identity.save('Board'); identity.update(platformEnabled=True)
        platform=Platform(identity,'https://example.test'); platform.telemetry_provider=self.device.telemetry
        platform.control_consumer=self.device.accept_control
        status=dict(id=identity.public()['id'],metadataRevision=1,lifecycle='REGISTERED',owner={'subject':'owner'})
        platform.status=status; platform.registered_revision=1
        session=status|dict(telemetrySession=SESSION,desiredControl=self.desired())
        platform.request=Mock(side_effect=[session,session,dict(state='NONE'),session,dict(state='NONE')])
        platform.tick(); platform.tick()
        calls=platform.request.call_args_list
        self.assertEqual(calls[0].args[:2],('POST','/telemetry-session'))
        self.assertEqual(calls[1].args[2]['reportSequence'],1)
        self.assertEqual(calls[3].args[2]['reportSequence'],2)
        self.assertEqual(calls[1].args[2]['telemetrySession'],SESSION)
        self.assertEqual(self.device.effective_settings()['match_id'],MATCH)
    def test_upgrade_existing_device_metadata_lost_response_retries_same_revision(self):
        identity=Identity(self.path/'identity.json'); identity.save('Board')
        original=identity.snapshot()
        old=dict(id=original['id'],metadataRevision=1,lifecycle='REGISTERED',owner=None,
                 softwareVersion='previous',capabilities=['PIN_PAIRING_V1','LOCAL_DISPLAY_V1'])
        client=Platform(identity,'https://example.test',version='new')
        client.request=Mock(side_effect=[old,OSError('lost metadata response')])
        with self.assertRaises(OSError): client.ensure_registration()
        self.assertEqual(identity.snapshot()['metadataRevision'],2)
        restarted=Platform(Identity(identity.path),'https://example.test',version='new')
        updated=old|dict(metadataRevision=2,softwareVersion='new',capabilities=Platform.CAPABILITIES)
        restarted.request=Mock(side_effect=[old,updated]); restarted.ensure_registration()
        self.assertEqual(restarted.request.call_args_list[1].args[2]['metadataRevision'],2)
        self.assertEqual(restarted.identity.snapshot()['id'],original['id'])
        self.assertEqual(restarted.identity.snapshot()['credential'],original['credential'])
        self.assertEqual(restarted.identity.snapshot()['name'],original['name'])
    def test_scoped_remote_feed_reads_private_routes_without_anonymous_transport(self):
        from test_live_state import ProjectionTests
        fixture=ProjectionTests(); fixture.setUp(); snapshot=fixture.snapshot
        snapshot['matchId']=MATCH
        transport=Mock(side_effect=[snapshot,dict(toRevision=snapshot['revision'],events=[])])
        feed=Feed('https://public.example.test',MATCH,device_request=transport)
        feed.connection=Mock(); feed.fetch(); feed.fetch_events()
        self.assertEqual(transport.call_args_list[0].args,('GET',f'/matches/{MATCH}/operations/snapshot'))
        self.assertEqual(transport.call_args_list[1].args,('GET',f'/matches/{MATCH}/operations/events?afterRevision=0&limit=20'))
        feed.connection.request.assert_not_called()
        self.assertEqual(feed.view()['match_id'],MATCH)
    def test_schedule_ack_requires_fresh_current_scoped_payload(self):
        desired=dict(revision=4,mode='SCHEDULE',effectiveMatchId=None,state='PENDING')
        self.device.accept_control(desired,True)
        self.device.matches=[dict(id=MATCH,status='SCHEDULED',teamA={'name':'One'},teamB={'name':'Two'})]
        self.device.catalog_at=time.monotonic(); self.device.schedule_revision=3
        self.device.render(); self.assertIsNone(self.device.control.ack)
        self.assertEqual(self.device.rendered_mode,'LOGO')
        self.device.schedule_revision=4; self.device.render()
        self.assertEqual(self.device.control.ack['status'],'APPLIED')
        self.assertEqual(self.device.rendered_mode,'SCHEDULE')
        report=self.device.telemetry(); self.assertEqual(report['feedState'],'FRESH')
        self.assertIsNone(report['renderedMatchId']); self.assertIsNone(report['selectedMatchId'])
        self.device.accept_control(desired|dict(state='APPLIED'),True)
        self.assertTrue(self.device.matches)
        self.device.catalog_at=time.monotonic()-20; self.device.render()
        self.assertEqual(self.device.rendered_mode,'LOGO')
        self.assertEqual(self.device.telemetry()['feedState'],'DELAYED')
        self.device.catalog_at=time.monotonic(); self.device.schedule_failed=True
        self.device.render()
        self.assertEqual(self.device.telemetry()['feedState'],'DISCONNECTED')
        self.assertIn('FEED',self.device.telemetry()['errors'])
        self.device.accept_control(None,False); self.assertEqual(self.device.matches,[])
        self.assertIsNone(self.device.schedule_revision)
    def test_schedule_router_only_uses_scoped_device_route(self):
        desired=dict(revision=4,mode='SCHEDULE',effectiveMatchId=None,state='PENDING')
        self.device.accept_control(desired,True)
        self.device.remote_request=Mock(return_value=dict(controlRevision=4,matches=[],truncated=False))
        self.device.stop=Mock(); self.device.stop.is_set.side_effect=[False,True]
        self.device.route()
        self.device.remote_request.assert_called_once_with('GET','/schedule')
        self.assertEqual(self.device.schedule_revision,4)
    def test_reconnect_does_not_interrupt_local_manual_takeover(self):
        self.device.apply(dict(mode='manual'))
        self.device.manual.command(dict(action='start',revision=0))
        self.device.accept_control(None,False)
        self.device.accept_control(dict(revision=5,mode='SCHEDULE',state='PENDING'),True)
        self.assertEqual(self.device.effective_settings()['mode'],'manual')
        self.assertTrue(self.device.manual.snapshot()['running'])
        self.assertEqual(self.device.control.ack['reason'],'LOCAL_TAKEOVER')
    def test_new_boot_opens_own_session_even_if_registration_returns_old_session(self):
        identity=Identity(self.path/'identity.json'); identity.save('Board'); identity.update(platformEnabled=True)
        platform=Platform(identity,'https://example.test'); platform.telemetry_provider=self.device.telemetry
        old=dict(id=identity.public()['id'],metadataRevision=1,lifecycle='REGISTERED',owner={'subject':'owner'},telemetrySession=SESSION)
        new=old|dict(telemetrySession=MATCH)
        platform.request=Mock(side_effect=[old,new,new,dict(state='NONE')])
        platform.tick()
        calls=platform.request.call_args_list
        self.assertEqual(calls[1].args[:2],('POST','/telemetry-session'))
        self.assertEqual(calls[2].args[2]['telemetrySession'],MATCH)
        self.assertEqual(calls[2].args[2]['reportSequence'],1)
