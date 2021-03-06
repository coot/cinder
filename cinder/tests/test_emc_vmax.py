# Copyright (c) 2012 - 2014 EMC Corporation, Inc.
# All Rights Reserved.
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

import os
import shutil
import tempfile
import time
from xml.dom.minidom import Document

import mock
import six

from cinder import exception
from cinder.openstack.common import log as logging
from cinder.openstack.common import loopingcall
from cinder import test
from cinder.volume.drivers.emc.emc_vmax_common import EMCVMAXCommon
from cinder.volume.drivers.emc.emc_vmax_fast import EMCVMAXFast
from cinder.volume.drivers.emc.emc_vmax_fc import EMCVMAXFCDriver
from cinder.volume.drivers.emc.emc_vmax_iscsi import EMCVMAXISCSIDriver
from cinder.volume.drivers.emc.emc_vmax_masking import EMCVMAXMasking
from cinder.volume.drivers.emc.emc_vmax_provision import EMCVMAXProvision
from cinder.volume.drivers.emc.emc_vmax_utils import EMCVMAXUtils
from cinder.volume import volume_types

LOG = logging.getLogger(__name__)
CINDER_EMC_CONFIG_DIR = '/etc/cinder/'


class EMC_StorageVolume(dict):
    pass


class CIM_StorageExtent(dict):
    pass


class SE_InitiatorMaskingGroup(dict):
    pass


class SE_ConcreteJob(dict):
    pass


class SE_StorageHardwareID(dict):
    pass


class SYMM_LunMasking(dict):
    pass


class CIM_DeviceMaskingGroup(dict):
    pass


class EMC_LunMaskingSCSIProtocolController(dict):
    pass


class CIM_TargetMaskingGroup(dict):
    pass


class EMC_StorageHardwareID(dict):
    pass


class Fake_CIMProperty():

    def fake_getCIMProperty(self):
        cimproperty = Fake_CIMProperty()
        cimproperty.value = True
        return cimproperty

    def fake_getBlockSizeCIMProperty(self):
        cimproperty = Fake_CIMProperty()
        cimproperty.value = '512'
        return cimproperty

    def fake_getConsumableBlocksCIMProperty(self):
        cimproperty = Fake_CIMProperty()
        cimproperty.value = '12345'
        return cimproperty

    def fake_getIsConcatenatedCIMProperty(self):
        cimproperty = Fake_CIMProperty()
        cimproperty.value = True
        return cimproperty

    def fake_getIsCompositeCIMProperty(self):
        cimproperty = Fake_CIMProperty()
        cimproperty.value = False
        return cimproperty

    def fake_getElementNameCIMProperty(self):
        cimproperty = Fake_CIMProperty()
        cimproperty.value = 'OS-myhost-MV'
        return cimproperty


class Fake_CIM_TierPolicyServiceCapabilities():

    def fake_getpolicyinstance(self):
        classinstance = Fake_CIM_TierPolicyServiceCapabilities()

        classcimproperty = Fake_CIMProperty()
        cimproperty = classcimproperty.fake_getCIMProperty()

        cimproperties = {u'SupportsTieringPolicies': cimproperty}
        classinstance.properties = cimproperties

        return classinstance


class FakeCIMInstanceName(dict):

    def fake_getinstancename(self, classname, bindings):
        instancename = FakeCIMInstanceName()
        for key in bindings:
            instancename[key] = bindings[key]
        instancename.classname = classname
        instancename.namespace = 'root/emc'
        return instancename


class FakeDB():

    def volume_update(self, context, volume_id, model_update):
        pass

    def volume_get(self, context, volume_id):
        conn = FakeEcomConnection()
        objectpath = {}
        objectpath['CreationClassName'] = 'Symm_StorageVolume'

        if volume_id == 'vol1':
            device_id = '1'
            objectpath['DeviceID'] = device_id
        else:
            objectpath['DeviceID'] = volume_id
        return conn.GetInstance(objectpath)


class EMCVMAXCommonData():
    wwpn1 = "123456789012345"
    wwpn2 = "123456789054321"
    connector = {'ip': '10.0.0.2',
                 'initiator': 'iqn.1993-08.org.debian: 01: 222',
                 'wwpns': [wwpn1, wwpn2],
                 'wwnns': ["223456789012345", "223456789054321"],
                 'host': 'fakehost'}

    target_wwns = [wwn[::-1] for wwn in connector['wwpns']]

    fabric_name_prefix = "fakeFabric"
    end_point_map = {connector['wwpns'][0]: [target_wwns[0]],
                     connector['wwpns'][1]: [target_wwns[1]]}
    device_map = {}
    for wwn in connector['wwpns']:
        fabric_name = ''.join([fabric_name_prefix,
                              wwn[-2:]])
        target_wwn = wwn[::-1]
        fabric_map = {'initiator_port_wwn_list': [wwn],
                      'target_port_wwn_list': [target_wwn]
                      }
        device_map[fabric_name] = fabric_map

    default_storage_group = (
        u'//10.10.10.10/root/emc: SE_DeviceMaskingGroup.InstanceID='
        '"SYMMETRIX+000198700440+OS_default_GOLD1_SG"')
    storage_system = 'SYMMETRIX+000195900551'
    port_group = 'OS-portgroup-PG'
    lunmaskctrl_id =\
        'SYMMETRIX+000195900551+OS-fakehost-gold-MV'
    lunmaskctrl_name =\
        'OS-fakehost-gold-MV'

    initiatorgroup_id =\
        'SYMMETRIX+000195900551+OS-fakehost-IG'
    initiatorgroup_name =\
        'OS-fakehost-IG'
    initiatorgroup_creationclass = 'SE_InitiatorMaskingGroup'

    storageextent_creationclass = 'CIM_StorageExtent'
    initiator1 = 'iqn.1993-08.org.debian: 01: 1a2b3c4d5f6g'
    stconf_service_creationclass = 'Symm_StorageConfigurationService'
    ctrlconf_service_creationclass = 'Symm_ControllerConfigurationService'
    elementcomp_service_creationclass = 'Symm_ElementCompositionService'
    storreloc_service_creationclass = 'Symm_StorageRelocationService'
    replication_service_creationclass = 'EMC_ReplicationService'
    vol_creationclass = 'Symm_StorageVolume'
    pool_creationclass = 'Symm_VirtualProvisioningPool'
    lunmask_creationclass = 'Symm_LunMaskingSCSIProtocolController'
    lunmask_creationclass2 = 'Symm_LunMaskingView'
    hostedservice_creationclass = 'CIM_HostedService'
    policycapability_creationclass = 'CIM_TierPolicyServiceCapabilities'
    policyrule_creationclass = 'Symm_TierPolicyRule'
    assoctierpolicy_creationclass = 'CIM_StorageTier'
    storagepool_creationclass = 'Symm_VirtualProvisioningPool'
    storagegroup_creationclass = 'CIM_DeviceMaskingGroup'
    hardwareid_creationclass = 'EMC_StorageHardwareID'
    storagepoolid = 'SYMMETRIX+000195900551+U+gold'
    storagegroupname = 'OS_default_GOLD1_SG'
    storagevolume_creationclass = 'EMC_StorageVolume'
    policyrule = 'gold'
    poolname = 'gold'
    totalmanagedspace_bits = '1000000000000'
    subscribedcapacity_bits = '500000000000'
    totalmanagedspace_gbs = 931
    subscribedcapacity_gbs = 466

    unit_creationclass = 'CIM_ProtocolControllerForUnit'
    storage_type = 'gold'
    keybindings = {'CreationClassName': u'Symm_StorageVolume',
                   'SystemName': u'SYMMETRIX+000195900551',
                   'DeviceID': u'1',
                   'SystemCreationClassName': u'Symm_StorageSystem'}

    keybindings2 = {'CreationClassName': u'Symm_StorageVolume',
                    'SystemName': u'SYMMETRIX+000195900551',
                    'DeviceID': u'99999',
                    'SystemCreationClassName': u'Symm_StorageSystem'}
    provider_location = {'classname': 'Symm_StorageVolume',
                         'keybindings': keybindings}
    provider_location2 = {'classname': 'Symm_StorageVolume',
                          'keybindings': keybindings2}

    properties = {'ConsumableBlocks': '12345',
                  'BlockSize': '512'}

    test_volume = {'name': 'vol1',
                   'size': 1,
                   'volume_name': 'vol1',
                   'id': '1',
                   'device_id': '1',
                   'provider_auth': None,
                   'project_id': 'project',
                   'display_name': 'vol1',
                   'display_description': 'test volume',
                   'volume_type_id': 'abc',
                   'provider_location': six.text_type(provider_location),
                   'status': 'available',
                   'host': 'fake-host'
                   }
    test_volume_v2 = {'name': 'vol1',
                      'size': 1,
                      'volume_name': 'vol1',
                      'id': 'vol1',
                      'device_id': '1',
                      'provider_auth': None,
                      'project_id': 'project',
                      'display_name': 'vol1',
                      'display_description': 'test volume',
                      'volume_type_id': 'abc',
                      'provider_location': six.text_type(provider_location),
                      'status': 'available',
                      'host': 'fake-host'
                      }
    test_failed_volume = {'name': 'failed_vol',
                          'size': 1,
                          'volume_name': 'failed_vol',
                          'id': '4',
                          'device_id': '4',
                          'provider_auth': None,
                          'project_id': 'project',
                          'display_name': 'failed_vol',
                          'display_description': 'test failed volume',
                          'volume_type_id': 'abc'}

    failed_delete_vol = {'name': 'failed_delete_vol',
                         'size': '-1',
                         'volume_name': 'failed_delete_vol',
                         'id': '99999',
                         'device_id': '99999',
                         'provider_auth': None,
                         'project_id': 'project',
                         'display_name': 'failed delete vol',
                         'display_description': 'failed delete volume',
                         'volume_type_id': 'abc',
                         'provider_location': six.text_type(provider_location2)
                         }

    test_source_volume = {'size': 1,
                          'volume_type_id': 'sourceid',
                          'display_name': 'sourceVolume',
                          'name': 'sourceVolume',
                          'volume_name': 'vmax-154326',
                          'id': 'vmax-154326',
                          'provider_auth': None,
                          'project_id':
                          'project', 'id': '2',
                          'provider_location':
                              six.text_type(provider_location),
                          'display_description': 'snapshot source volume'}

    location_info = {'location_info': '000195900551#silver#None',
                     'storage_protocol': 'ISCSI'}
    test_host = {'capabilities': location_info,
                 'host': 'fake_host'}

    initiatorNames = ["123456789012345", "123456789054321"]
    test_ctxt = {}
    new_type = {}
    diff = {}


class FakeLookupService():
    def get_device_mapping_from_network(self, initiator_wwns, target_wwns):
        return EMCVMAXCommonData.device_map


class FakeEcomConnection():

    def __init__(self, *args, **kwargs):
        self.data = EMCVMAXCommonData()

    def InvokeMethod(self, MethodName, Service, ElementName=None, InPool=None,
                     ElementType=None, Size=None,
                     SyncType=None, SourceElement=None, TargetElement=None,
                     Operation=None, Synchronization=None,
                     TheElements=None, TheElement=None,
                     LUNames=None, InitiatorPortIDs=None, DeviceAccesses=None,
                     ProtocolControllers=None,
                     MaskingGroup=None, Members=None,
                     HardwareId=None, ElementSource=None, EMCInPools=None,
                     CompositeType=None, EMCNumberOfMembers=None,
                     EMCBindElements=None,
                     InElements=None, TargetPool=None, RequestedState=None,
                     GroupName=None, Type=None, InitiatorMaskingGroup=None,
                     DeviceMaskingGroup=None, TargetMaskingGroup=None):

        rc = 0L
        myjob = SE_ConcreteJob()
        myjob.classname = 'SE_ConcreteJob'
        myjob['InstanceID'] = '9999'
        myjob['status'] = 'success'
        myjob['type'] = ElementName

        if Size == -1073741824 and \
                MethodName == 'CreateOrModifyCompositeElement':
            rc = 0L
            myjob = SE_ConcreteJob()
            myjob.classname = 'SE_ConcreteJob'
            myjob['InstanceID'] = '99999'
            myjob['status'] = 'success'
            myjob['type'] = 'failed_delete_vol'
        elif ElementName is None and \
                MethodName == 'CreateOrModifyCompositeElement':
            rc = 0L
            myjob = SE_ConcreteJob()
            myjob.classname = 'SE_ConcreteJob'
            myjob['InstanceID'] = '9999'
            myjob['status'] = 'success'
            myjob['type'] = 'vol1'

        if ElementName == 'failed_vol' and \
                MethodName == 'CreateOrModifyElementFromStoragePool':
            rc = 10L
            myjob['status'] = 'failure'

        elif TheElements and \
                TheElements[0]['DeviceID'] == '99999' and \
                MethodName == 'EMCReturnToStoragePool':
            rc = 10L
            myjob['status'] = 'failure'
        elif HardwareId:
            rc = 0L
            targetendpoints = {}
            endpoints = []
            endpoint = {}
            endpoint['Name'] = (EMCVMAXCommonData.end_point_map[
                EMCVMAXCommonData.connector['wwpns'][0]])
            endpoints.append(endpoint)
            endpoint2 = {}
            endpoint2['Name'] = (EMCVMAXCommonData.end_point_map[
                EMCVMAXCommonData.connector['wwpns'][1]])
            endpoints.append(endpoint2)
            targetendpoints['TargetEndpoints'] = endpoints
            return rc, targetendpoints

        job = {'Job': myjob}
        return rc, job

    def EnumerateInstanceNames(self, name):
        result = None
        if name == 'EMC_StorageConfigurationService':
            result = self._enum_stconfsvcs()
        elif name == 'EMC_ControllerConfigurationService':
            result = self._enum_ctrlconfsvcs()
        elif name == 'Symm_ElementCompositionService':
            result = self._enum_elemcompsvcs()
        elif name == 'Symm_StorageRelocationService':
            result = self._enum_storrelocsvcs()
        elif name == 'EMC_ReplicationService':
            result = self._enum_replicsvcs()
        elif name == 'EMC_VirtualProvisioningPool':
            result = self._enum_pools()
        elif name == 'EMC_StorageVolume':
            result = self._enum_storagevolumes()
        elif name == 'Symm_StorageVolume':
            result = self._enum_storagevolumes()
        elif name == 'CIM_ProtocolControllerForUnit':
            result = self._enum_unitnames()
        elif name == 'EMC_LunMaskingSCSIProtocolController':
            result = self._enum_lunmaskctrls()
        elif name == 'EMC_StorageProcessorSystem':
            result = self._enum_processors()
        elif name == 'EMC_StorageHardwareIDManagementService':
            result = self._enum_hdwidmgmts()
        elif name == 'SE_StorageHardwareID':
            result = self._enum_storhdwids()
        elif name == 'EMC_StorageSystem':
            result = self._enum_storage_system()
        elif name == 'Symm_TierPolicyRule':
            result = self._enum_policyrules()
        else:
            result = self._default_enum()
        return result

    def EnumerateInstances(self, name):
        result = None
        if name == 'EMC_VirtualProvisioningPool':
            result = self._enum_pool_details()
        elif name == 'SE_StorageHardwareID':
            result = self._enum_storhdwids()
        else:
            result = self._default_enum()
        return result

    def GetInstance(self, objectpath, LocalOnly=False):

        try:
            name = objectpath['CreationClassName']
        except KeyError:
            name = objectpath.classname
        result = None
        if name == 'Symm_StorageVolume':
            result = self._getinstance_storagevolume(objectpath)
        elif name == 'CIM_ProtocolControllerForUnit':
            result = self._getinstance_unit(objectpath)
        elif name == 'SE_ConcreteJob':
            result = self._getinstance_job(objectpath)
        elif name == 'SE_StorageSynchronized_SV_SV':
            result = self._getinstance_syncsvsv(objectpath)
        elif name == 'Symm_TierPolicyServiceCapabilities':
            result = self._getinstance_policycapabilities(objectpath)
        elif name == 'CIM_TierPolicyServiceCapabilities':
            result = self._getinstance_policycapabilities(objectpath)
        elif name == 'SE_InitiatorMaskingGroup':
            result = self._getinstance_initiatormaskinggroup(objectpath)
        elif name == 'SE_StorageHardwareID':
            result = self._getinstance_storagehardwareid(objectpath)
        elif name == 'EMC_StorageHardwareID':
            result = self._getinstance_storagehardwareid(objectpath)
        elif name == 'Symm_VirtualProvisioningPool':
            result = self._getinstance_pool(objectpath)
        else:
            result = self._default_getinstance(objectpath)

        return result

    def DeleteInstance(self, objectpath):
        pass

    def Associators(self, objectpath, ResultClass='EMC_StorageHardwareID'):
        result = None
        if ResultClass == 'EMC_StorageHardwareID':
            result = self._assoc_hdwid()
        elif ResultClass == 'EMC_iSCSIProtocolEndpoint':
            result = self._assoc_endpoint()
        elif ResultClass == 'EMC_StorageVolume':
            result = self._assoc_storagevolume(objectpath)
        elif ResultClass == 'Symm_LunMaskingView':
            result = self._assoc_maskingview()
        elif ResultClass == 'CIM_DeviceMaskingGroup':
            result = self._assoc_storagegroup()
        elif ResultClass == 'CIM_StorageExtent':
            result = self._enum_storage_extent()
        elif ResultClass == 'EMC_LunMaskingSCSIProtocolController':
            result = self._assoc_lunmaskctrls()
        elif ResultClass == 'CIM_TargetMaskingGroup':
            result = self._assoc_portgroup()
        else:
            result = self._default_assoc(objectpath)
        return result

    def AssociatorNames(self, objectpath,
                        ResultClass='default', AssocClass='default'):
        result = None

        if ResultClass == 'EMC_LunMaskingSCSIProtocolController':
            result = self._assocnames_lunmaskctrl()
        elif AssocClass == 'CIM_HostedService':
            result = self._assocnames_hostedservice()
        elif ResultClass == 'CIM_TierPolicyServiceCapabilities':
            result = self._assocnames_policyCapabilities()
        elif ResultClass == 'Symm_TierPolicyRule':
            result = self._assocnames_policyrule()
        elif AssocClass == 'CIM_AssociatedTierPolicy':
            result = self._assocnames_assoctierpolicy()
        elif ResultClass == 'CIM_StoragePool':
            result = self._assocnames_storagepool()
        elif ResultClass == 'EMC_VirtualProvisioningPool':
            result = self._assocnames_storagepool()
        elif ResultClass == 'CIM_DeviceMaskingGroup':
            result = self._assocnames_storagegroup()
        elif ResultClass == 'EMC_StorageVolume':
            result = self._enum_storagevolumes()
        elif ResultClass == 'Symm_StorageVolume':
            result = self._enum_storagevolumes()
        elif ResultClass == 'SE_InitiatorMaskingGroup':
            result = self._enum_initiatorMaskingGroup()
        elif ResultClass == 'CIM_InitiatorMaskingGroup':
            result = self._enum_initiatorMaskingGroup()
        elif ResultClass == 'CIM_StorageExtent':
            result = self._enum_storage_extent()
        elif ResultClass == 'SE_StorageHardwareID':
            result = self._enum_storhdwids()
        elif ResultClass == 'Symm_FCSCSIProtocolEndpoint':
            result = self._enum_fcscsiendpoint()
        elif ResultClass == 'CIM_TargetMaskingGroup':
            result = self._assocnames_portgroup()

        else:
            result = self._default_assocnames(objectpath)
        return result

    def ReferenceNames(self, objectpath,
                       ResultClass='CIM_ProtocolControllerForUnit'):
        result = None
        if ResultClass == 'CIM_ProtocolControllerForUnit':
            result = self._ref_unitnames2()
        else:
            result = self._default_ref(objectpath)
        return result

    def _ref_unitnames(self):
        unitnames = []
        unitname = {}

        dependent = {}
        dependent['CreationClassName'] = self.data.vol_creationclass
        dependent['DeviceID'] = self.data.test_volume['id']
        dependent['ElementName'] = self.data.test_volume['name']
        dependent['SystemName'] = self.data.storage_system

        antecedent = {}
        antecedent['CreationClassName'] = self.data.lunmask_creationclass
        antecedent['DeviceID'] = self.data.lunmaskctrl_id
        antecedent['SystemName'] = self.data.storage_system

        unitname['Dependent'] = dependent
        unitname['Antecedent'] = antecedent
        unitname['CreationClassName'] = self.data.unit_creationclass
        unitnames.append(unitname)

        return unitnames

    def _ref_unitnames2(self):
        unitnames = []
        unitname = {}

        dependent = {}
        dependent['CreationClassName'] = self.data.vol_creationclass
        dependent['DeviceID'] = self.data.test_volume['id']
        dependent['ElementName'] = self.data.test_volume['name']
        dependent['SystemName'] = self.data.storage_system

        antecedent = SYMM_LunMasking()
        antecedent['CreationClassName'] = self.data.lunmask_creationclass2
        antecedent['SystemName'] = self.data.storage_system
        classcimproperty = Fake_CIMProperty()
        elementName = (
            classcimproperty.fake_getElementNameCIMProperty())
        properties = {u'ElementName': elementName}
        antecedent.properties = properties

        unitname['Dependent'] = dependent
        unitname['Antecedent'] = antecedent
        unitname['CreationClassName'] = self.data.unit_creationclass
        unitnames.append(unitname)

        return unitnames

    def _default_ref(self, objectpath):
        return objectpath

    def _assoc_hdwid(self):
        assocs = []
        assoc = EMC_StorageHardwareID()
        assoc['StorageID'] = self.data.connector['initiator']
        assoc['SystemName'] = self.data.storage_system
        assoc['CreationClassName'] = 'EMC_StorageHardwareID'
        assoc.path = assoc
        assocs.append(assoc)
        for wwpn in self.data.connector['wwpns']:
            assoc2 = EMC_StorageHardwareID()
            assoc2['StorageID'] = wwpn
            assoc2['SystemName'] = self.data.storage_system
            assoc2['CreationClassName'] = 'EMC_StorageHardwareID'
            assoc2.path = assoc2
            assocs.append(assoc2)
        assocs.append(assoc)
        return assocs

    def _assoc_endpoint(self):
        assocs = []
        assoc = {}
        assoc['Name'] = 'iqn.1992-04.com.emc: 50000973f006dd80'
        assoc['SystemName'] = self.data.storage_system
        assocs.append(assoc)
        return assocs

    def _assoc_storagegroup(self):
        assocs = []
        assoc = CIM_DeviceMaskingGroup()
        assoc['ElementName'] = 'OS_default_GOLD1_SG'
        assoc['SystemName'] = self.data.storage_system
        assoc['CreationClassName'] = 'CIM_DeviceMaskingGroup'
        assoc.path = assoc
        assocs.append(assoc)
        return assocs

    def _assoc_portgroup(self):
        assocs = []
        assoc = CIM_TargetMaskingGroup()
        assoc['ElementName'] = self.data.port_group
        assoc['SystemName'] = self.data.storage_system
        assoc['CreationClassName'] = 'CIM_TargetMaskingGroup'
        assoc.path = assoc
        assocs.append(assoc)
        return assocs

    def _assoc_lunmaskctrls(self):
        ctrls = []
        ctrl = EMC_LunMaskingSCSIProtocolController()
        ctrl['CreationClassName'] = self.data.lunmask_creationclass
        ctrl['DeviceID'] = self.data.lunmaskctrl_id
        ctrl['SystemName'] = self.data.storage_system
        ctrl['ElementName'] = self.data.lunmaskctrl_name
        ctrl.path = ctrl
        ctrls.append(ctrl)
        return ctrls

    # Added test for EMC_StorageVolume associators
    def _assoc_storagevolume(self, objectpath):
        assocs = []
        if 'type' not in objectpath:
            vol = self.data.test_volume
        elif objectpath['type'] == 'failed_delete_vol':
            vol = self.data.failed_delete_vol
        elif objectpath['type'] == 'vol1':
            vol = self.data.test_volume
        elif objectpath['type'] == 'appendVolume':
            vol = self.data.test_volume
        elif objectpath['type'] == 'failed_vol':
            vol = self.data.test_failed_volume
        elif objectpath['type'] == 'TargetBaseVol':
            vol = self.data.test_failed_volume
        else:
            return None

        vol['DeviceID'] = vol['device_id']
        assoc = self._getinstance_storagevolume(vol)
        assocs.append(assoc)
        return assocs

    def _assoc_maskingview(self):
        assocs = []
        assoc = SYMM_LunMasking()
        assoc['Name'] = 'myMaskingView'
        assoc['SystemName'] = self.data.storage_system
        assoc['CreationClassName'] = 'Symm_LunMaskingView'
        assoc['DeviceID'] = '1234'
        assoc['SystemCreationClassName'] = '1234'
        assoc['ElementName'] = 'OS-fakehost-gold-I-MV'
        assoc.classname = assoc['CreationClassName']
        assoc.path = assoc
        assocs.append(assoc)
        return assocs

    def _default_assoc(self, objectpath):
        return objectpath

    def _assocnames_lunmaskctrl(self):
        return self._enum_lunmaskctrls()

    def _assocnames_hostedservice(self):
        return self._enum_hostedservice()

    def _assocnames_policyCapabilities(self):
        return self._enum_policycapabilities()

    def _assocnames_policyrule(self):
        return self._enum_policyrules()

    def _assocnames_assoctierpolicy(self):
        return self._enum_assoctierpolicy()

    def _assocnames_storagepool(self):
        return self._enum_storagepool()

    def _assocnames_storagegroup(self):
        return self._enum_storagegroup()

    def _assocnames_storagevolume(self):
        return self._enum_storagevolume()

    def _assocnames_portgroup(self):
        return self._enum_portgroup()

    def _default_assocnames(self, objectpath):
        return objectpath

    def _getinstance_storagevolume(self, objectpath):
        foundinstance = None
        instance = EMC_StorageVolume()
        vols = self._enum_storagevolumes()

        for vol in vols:
            if vol['DeviceID'] == objectpath['DeviceID']:
                instance = vol
                break
        if not instance:
            foundinstance = None
        else:
            foundinstance = instance
        return foundinstance

    def _getinstance_lunmask(self):
        lunmask = {}
        lunmask['CreationClassName'] = self.data.lunmask_creationclass
        lunmask['DeviceID'] = self.data.lunmaskctrl_id
        lunmask['SystemName'] = self.data.storage_system
        return lunmask

    def _getinstance_initiatormaskinggroup(self, objectpath):

        initiatorgroup = SE_InitiatorMaskingGroup()
        initiatorgroup['CreationClassName'] = (
            self.data.initiatorgroup_creationclass)
        initiatorgroup['DeviceID'] = self.data.initiatorgroup_id
        initiatorgroup['SystemName'] = self.data.storage_system
        initiatorgroup['ElementName'] = self.data.initiatorgroup_name
        initiatorgroup.path = initiatorgroup
        return initiatorgroup

    def _getinstance_storagehardwareid(self, objectpath):
        hardwareid = SE_StorageHardwareID()
        hardwareid['CreationClassName'] = self.data.hardwareid_creationclass
        hardwareid['SystemName'] = self.data.storage_system
        hardwareid['StorageID'] = self.data.connector['wwpns'][0]
        hardwareid.path = hardwareid
        return hardwareid

    def _getinstance_pool(self, objectpath):
        pool = {}
        pool['CreationClassName'] = 'Symm_VirtualProvisioningPool'
        pool['ElementName'] = 'gold'
        pool['SystemName'] = self.data.storage_system
        pool['TotalManagedSpace'] = self.data.totalmanagedspace_bits
        pool['EMCSubscribedCapacity'] = self.data.subscribedcapacity_bits
        return pool

    def _getinstance_unit(self, objectpath):
        unit = {}

        dependent = {}
        dependent['CreationClassName'] = self.data.vol_creationclass
        dependent['DeviceID'] = self.data.test_volume['id']
        dependent['ElementName'] = self.data.test_volume['name']
        dependent['SystemName'] = self.data.storage_system

        antecedent = {}
        antecedent['CreationClassName'] = self.data.lunmask_creationclass
        antecedent['DeviceID'] = self.data.lunmaskctrl_id
        antecedent['SystemName'] = self.data.storage_system

        unit['Dependent'] = dependent
        unit['Antecedent'] = antecedent
        unit['CreationClassName'] = self.data.unit_creationclass
        unit['DeviceNumber'] = '1'

        return unit

    def _getinstance_job(self, jobpath):
        jobinstance = {}
        jobinstance['InstanceID'] = '9999'
        if jobpath['status'] == 'failure':
            jobinstance['JobState'] = 10
            jobinstance['ErrorCode'] = 99
            jobinstance['ErrorDescription'] = 'Failure'
        else:
            jobinstance['JobState'] = 7
            jobinstance['ErrorCode'] = 0
            jobinstance['ErrorDescription'] = ''
        return jobinstance

    def _getinstance_policycapabilities(self, policycapabilitypath):
        instance = Fake_CIM_TierPolicyServiceCapabilities()
        fakeinstance = instance.fake_getpolicyinstance()
        return fakeinstance

    def _getinstance_syncsvsv(self, objectpath):
        svInstance = {}
        svInstance['SyncedElement'] = 'SyncedElement'
        svInstance['SystemElement'] = 'SystemElement'
        svInstance['PercentSynced'] = 100
        return svInstance

    def _default_getinstance(self, objectpath):
        return objectpath

    def _enum_stconfsvcs(self):
        conf_services = []
        conf_service = {}
        conf_service['SystemName'] = self.data.storage_system
        conf_service['CreationClassName'] =\
            self.data.stconf_service_creationclass
        conf_services.append(conf_service)
        return conf_services

    def _enum_ctrlconfsvcs(self):
        conf_services = []
        conf_service = {}
        conf_service['SystemName'] = self.data.storage_system
        conf_service['CreationClassName'] =\
            self.data.ctrlconf_service_creationclass
        conf_services.append(conf_service)
        return conf_services

    def _enum_elemcompsvcs(self):
        comp_services = []
        comp_service = {}
        comp_service['SystemName'] = self.data.storage_system
        comp_service['CreationClassName'] =\
            self.data.elementcomp_service_creationclass
        comp_services.append(comp_service)
        return comp_services

    def _enum_storrelocsvcs(self):
        reloc_services = []
        reloc_service = {}
        reloc_service['SystemName'] = self.data.storage_system
        reloc_service['CreationClassName'] =\
            self.data.storreloc_service_creationclass
        reloc_services.append(reloc_service)
        return reloc_services

    def _enum_replicsvcs(self):
        replic_services = []
        replic_service = {}
        replic_service['SystemName'] = self.data.storage_system
        replic_service['CreationClassName'] =\
            self.data.replication_service_creationclass
        replic_services.append(replic_service)
        return replic_services

    def _enum_pools(self):
        pools = []
        pool = {}
        pool['InstanceID'] = self.data.storage_system + '+U+' +\
            self.data.storage_type
        pool['CreationClassName'] = 'Symm_VirtualProvisioningPool'
        pool['ElementName'] = 'gold'
        pools.append(pool)
        return pools

    def _enum_pool_details(self):
        pools = []
        pool = {}
        pool['InstanceID'] = self.data.storage_system + '+U+' +\
            self.data.storage_type
        pool['CreationClassName'] = 'Symm_VirtualProvisioningPool'
        pool['TotalManagedSpace'] = 12345678
        pool['RemainingManagedSpace'] = 123456
        pools.append(pool)
        return pools

    def _enum_storagevolumes(self):
        vols = []

        vol = EMC_StorageVolume()
        vol['name'] = self.data.test_volume['name']
        vol['CreationClassName'] = 'Symm_StorageVolume'
        vol['ElementName'] = self.data.test_volume['name']
        vol['DeviceID'] = self.data.test_volume['id']
        vol['SystemName'] = self.data.storage_system

        # Added vol to vol.path
        vol['SystemCreationClassName'] = 'Symm_StorageSystem'
        vol.path = vol
        vol.path.classname = vol['CreationClassName']

        classcimproperty = Fake_CIMProperty()
        blocksizecimproperty = classcimproperty.fake_getBlockSizeCIMProperty()
        consumableBlockscimproperty = (
            classcimproperty.fake_getConsumableBlocksCIMProperty())
        isCompositecimproperty = (
            classcimproperty.fake_getIsCompositeCIMProperty())
        properties = {u'ConsumableBlocks': blocksizecimproperty,
                      u'BlockSize': consumableBlockscimproperty,
                      u'IsComposite': isCompositecimproperty}
        vol.properties = properties

        name = {}
        name['classname'] = 'Symm_StorageVolume'
        keys = {}
        keys['CreationClassName'] = 'Symm_StorageVolume'
        keys['SystemName'] = self.data.storage_system
        keys['DeviceID'] = vol['DeviceID']
        keys['SystemCreationClassName'] = 'Symm_StorageSystem'
        name['keybindings'] = keys

        vol['provider_location'] = str(name)

        vols.append(vol)

        failed_delete_vol = EMC_StorageVolume()
        failed_delete_vol['name'] = 'failed_delete_vol'
        failed_delete_vol['CreationClassName'] = 'Symm_StorageVolume'
        failed_delete_vol['ElementName'] = 'failed_delete_vol'
        failed_delete_vol['DeviceID'] = '99999'
        failed_delete_vol['SystemName'] = self.data.storage_system
        # Added vol to vol.path
        failed_delete_vol['SystemCreationClassName'] = 'Symm_StorageSystem'
        failed_delete_vol.path = failed_delete_vol
        failed_delete_vol.path.classname =\
            failed_delete_vol['CreationClassName']
        vols.append(failed_delete_vol)

        failed_vol = EMC_StorageVolume()
        failed_vol['name'] = 'failed__vol'
        failed_vol['CreationClassName'] = 'Symm_StorageVolume'
        failed_vol['ElementName'] = 'failed_vol'
        failed_vol['DeviceID'] = '4'
        failed_vol['SystemName'] = self.data.storage_system
        # Added vol to vol.path
        failed_vol['SystemCreationClassName'] = 'Symm_StorageSystem'
        failed_vol.path = failed_vol
        failed_vol.path.classname =\
            failed_vol['CreationClassName']

        name_failed = {}
        name_failed['classname'] = 'Symm_StorageVolume'
        keys_failed = {}
        keys_failed['CreationClassName'] = 'Symm_StorageVolume'
        keys_failed['SystemName'] = self.data.storage_system
        keys_failed['DeviceID'] = failed_vol['DeviceID']
        keys_failed['SystemCreationClassName'] = 'Symm_StorageSystem'
        name_failed['keybindings'] = keys_failed
        failed_vol['provider_location'] = str(name_failed)

        vols.append(failed_vol)

        return vols

    def _enum_initiatorMaskingGroup(self):
        initatorgroups = []
        initatorgroup = {}
        initatorgroup['CreationClassName'] = (
            self.data.initiatorgroup_creationclass)
        initatorgroup['DeviceID'] = self.data.initiatorgroup_id
        initatorgroup['SystemName'] = self.data.storage_system
        initatorgroup['ElementName'] = self.data.initiatorgroup_name
        initatorgroups.append(initatorgroup)
        return initatorgroups

    def _enum_storage_system(self):
        storagesystems = []
        storagesystem = {}
        storagesystem['SystemName'] = self.data.storage_system
        storagesystem['Name'] = self.data.storage_system
        storagesystems.append(storagesystem)
        return storagesystems

    def _enum_storage_extent(self):
        storageExtents = []
        storageExtent = CIM_StorageExtent()
        storageExtent['CreationClassName'] = (
            self.data.storageextent_creationclass)

        classcimproperty = Fake_CIMProperty()
        isConcatenatedcimproperty = (
            classcimproperty.fake_getIsConcatenatedCIMProperty())
        properties = {u'IsConcatenated': isConcatenatedcimproperty}
        storageExtent.properties = properties

        storageExtents.append(storageExtent)
        return storageExtents

    def _enum_lunmaskctrls(self):
        ctrls = []
        ctrl = {}
        ctrl['CreationClassName'] = self.data.lunmask_creationclass
        ctrl['DeviceID'] = self.data.lunmaskctrl_id
        ctrl['SystemName'] = self.data.storage_system
        ctrl['ElementName'] = self.data.lunmaskctrl_name
        ctrls.append(ctrl)
        return ctrls

    def _enum_hostedservice(self):
        hostedservices = []
        hostedservice = {}
        hostedservice['CreationClassName'] = (
            self.data.hostedservice_creationclass)
        hostedservice['SystemName'] = self.data.storage_system
        hostedservices.append(hostedservice)
        return hostedservices

    def _enum_policycapabilities(self):
        policycapabilities = []
        policycapability = {}
        policycapability['CreationClassName'] = (
            self.data.policycapability_creationclass)
        policycapability['SystemName'] = self.data.storage_system

        propertiesList = []
        CIMProperty = {'is_array': True}
        properties = {u'SupportedTierFeatures': CIMProperty}
        propertiesList.append(properties)
        policycapability['Properties'] = propertiesList

        policycapabilities.append(policycapability)

        return policycapabilities

    def _enum_policyrules(self):
        policyrules = []
        policyrule = {}
        policyrule['CreationClassName'] = self.data.policyrule_creationclass
        policyrule['SystemName'] = self.data.storage_system
        policyrule['PolicyRuleName'] = self.data.policyrule
        policyrules.append(policyrule)
        return policyrules

    def _enum_assoctierpolicy(self):
        assoctierpolicies = []
        assoctierpolicy = {}
        assoctierpolicy['CreationClassName'] = (
            self.data.assoctierpolicy_creationclass)
        assoctierpolicies.append(assoctierpolicy)
        return assoctierpolicies

    def _enum_storagepool(self):
        storagepools = []
        storagepool = {}
        storagepool['CreationClassName'] = self.data.storagepool_creationclass
        storagepool['InstanceID'] = self.data.storagepoolid
        storagepool['ElementName'] = 'gold'
        storagepools.append(storagepool)
        return storagepools

    def _enum_storagegroup(self):
        storagegroups = []
        storagegroup = {}
        storagegroup['CreationClassName'] = (
            self.data.storagegroup_creationclass)
        storagegroup['ElementName'] = self.data.storagegroupname
        storagegroups.append(storagegroup)
        return storagegroups

    def _enum_storagevolume(self):
        storagevolumes = []
        storagevolume = {}
        storagevolume['CreationClassName'] = (
            self.data.storagevolume_creationclass)
        storagevolumes.append(storagevolume)
        return storagevolumes

    def _enum_hdwidmgmts(self):
        services = []
        srv = {}
        srv['SystemName'] = self.data.storage_system
        services.append(srv)
        return services

    def _enum_storhdwids(self):
        storhdwids = []
        hdwid = SE_StorageHardwareID()
        hdwid['CreationClassName'] = self.data.hardwareid_creationclass
        hdwid['StorageID'] = self.data.connector['wwpns'][0]

        hdwid.path = hdwid
        storhdwids.append(hdwid)
        return storhdwids

    def _enum_fcscsiendpoint(self):
        wwns = []
        wwn = {}
        wwn['Name'] = "5000090000000000"
        wwns.append(wwn)
        return wwns

    def _enum_portgroup(self):
        portgroups = []
        portgroup = {}
        portgroup['CreationClassName'] = (
            'CIM_TargetMaskingGroup')
        portgroup['ElementName'] = self.data.port_group
        portgroups.append(portgroup)
        return portgroups

    def _default_enum(self):
        names = []
        name = {}
        name['Name'] = 'default'
        names.append(name)
        return names


class EMCVMAXISCSIDriverNoFastTestCase(test.TestCase):
    def setUp(self):

        self.data = EMCVMAXCommonData()

        self.tempdir = tempfile.mkdtemp()
        super(EMCVMAXISCSIDriverNoFastTestCase, self).setUp()
        self.config_file_path = None
        self.config_file_1364232 = None
        self.create_fake_config_file_no_fast()
        self.addCleanup(self._cleanup)

        configuration = mock.Mock()
        configuration.safe_get.return_value = 'ISCSINoFAST'
        configuration.cinder_emc_config_file = self.config_file_path
        configuration.config_group = 'ISCSINoFAST'

        self.stubs.Set(EMCVMAXISCSIDriver, 'smis_do_iscsi_discovery',
                       self.fake_do_iscsi_discovery)
        self.stubs.Set(EMCVMAXCommon, '_get_ecom_connection',
                       self.fake_ecom_connection)
        instancename = FakeCIMInstanceName()
        self.stubs.Set(EMCVMAXUtils, 'get_instance_name',
                       instancename.fake_getinstancename)
        self.stubs.Set(time, 'sleep',
                       self.fake_sleep)

        driver = EMCVMAXISCSIDriver(configuration=configuration)
        driver.db = FakeDB()
        self.driver = driver
        self.driver.utils = EMCVMAXUtils(object)

    def create_fake_config_file_no_fast(self):

        doc = Document()
        emc = doc.createElement("EMC")
        doc.appendChild(emc)

        array = doc.createElement("Array")
        arraytext = doc.createTextNode("1234567891011")
        emc.appendChild(array)
        array.appendChild(arraytext)

        ecomserverip = doc.createElement("EcomServerIp")
        ecomserveriptext = doc.createTextNode("1.1.1.1")
        emc.appendChild(ecomserverip)
        ecomserverip.appendChild(ecomserveriptext)

        ecomserverport = doc.createElement("EcomServerPort")
        ecomserverporttext = doc.createTextNode("10")
        emc.appendChild(ecomserverport)
        ecomserverport.appendChild(ecomserverporttext)

        ecomusername = doc.createElement("EcomUserName")
        ecomusernametext = doc.createTextNode("user")
        emc.appendChild(ecomusername)
        ecomusername.appendChild(ecomusernametext)

        ecompassword = doc.createElement("EcomPassword")
        ecompasswordtext = doc.createTextNode("pass")
        emc.appendChild(ecompassword)
        ecompassword.appendChild(ecompasswordtext)

        portgroup = doc.createElement("PortGroup")
        portgrouptext = doc.createTextNode(self.data.port_group)
        portgroup.appendChild(portgrouptext)

        portgroups = doc.createElement("PortGroups")
        portgroups.appendChild(portgroup)
        emc.appendChild(portgroups)

        pool = doc.createElement("Pool")
        pooltext = doc.createTextNode("gold")
        emc.appendChild(pool)
        pool.appendChild(pooltext)

        array = doc.createElement("Array")
        arraytext = doc.createTextNode("0123456789")
        emc.appendChild(array)
        array.appendChild(arraytext)

        timeout = doc.createElement("Timeout")
        timeouttext = doc.createTextNode("0")
        emc.appendChild(timeout)
        timeout.appendChild(timeouttext)

        filename = 'cinder_emc_config_ISCSINoFAST.xml'

        self.config_file_path = self.tempdir + '/' + filename

        f = open(self.config_file_path, 'w')
        doc.writexml(f)
        f.close()

    # Create XML config file with newlines and whitespaces
    # Bug #1364232
    def create_fake_config_file_1364232(self):
        filename = 'cinder_emc_config_1364232.xml'
        self.config_file_1364232 = self.tempdir + '/' + filename
        text_file = open(self.config_file_1364232, "w")
        text_file.write("<?xml version='1.0' encoding='UTF-8'?>\n<EMC>\n"
                        "<EcomServerIp>10.10.10.10</EcomServerIp>\n"
                        "<EcomServerPort>5988</EcomServerPort>\n"
                        "<EcomUserName>user\t</EcomUserName>\n"
                        "<EcomPassword>password</EcomPassword>\n"
                        "<PortGroups><PortGroup>OS-PORTGROUP1-PG"
                        "</PortGroup><PortGroup>OS-PORTGROUP2-PG"
                        "                </PortGroup>\n"
                        "<PortGroup>OS-PORTGROUP3-PG</PortGroup>"
                        "<PortGroup>OS-PORTGROUP4-PG</PortGroup>"
                        "</PortGroups>\n<Array>000198700439"
                        "              \n</Array>\n<Pool>FC_SLVR1\n"
                        "</Pool>\n<FastPolicy>SILVER1</FastPolicy>\n"
                        "</EMC>")
        text_file.close()

    def fake_ecom_connection(self):
        conn = FakeEcomConnection()
        return conn

    def fake_do_iscsi_discovery(self, volume):
        output = []
        item = '10.10.0.50: 3260,1 iqn.1992-04.com.emc: 50000973f006dd80'
        output.append(item)
        return output

    def fake_sleep(self, seconds):
        return

    def test_wait_for_job_complete(self):
        myjob = SE_ConcreteJob()
        myjob.classname = 'SE_ConcreteJob'
        myjob['InstanceID'] = '9999'
        myjob['status'] = 'success'
        myjob['type'] = 'type'
        myjob['CreationClassName'] = 'SE_ConcreteJob'
        myjob['Job'] = myjob
        conn = self.fake_ecom_connection()

        self.driver.utils._is_job_finished = mock.Mock(
            return_value = True)
        rc = self.driver.utils._wait_for_job_complete(conn, myjob)
        self.assertIsNone(rc)
        self.driver.utils._is_job_finished.assert_called_once_with(
            conn, myjob)
        self.assertEqual(
            True,
            self.driver.utils._is_job_finished.return_value)
        self.driver.utils._is_job_finished.reset_mock()

        # Save the original state and restore it after this test
        loopingcall_orig = loopingcall.FixedIntervalLoopingCall
        loopingcall.FixedIntervalLoopingCall = mock.Mock()
        rc = self.driver.utils._wait_for_job_complete(conn, myjob)
        self.assertIsNone(rc)
        loopingcall.FixedIntervalLoopingCall.assert_called_once_with(
            mock.ANY)
        loopingcall.FixedIntervalLoopingCall.reset_mock()
        loopingcall.FixedIntervalLoopingCall = loopingcall_orig

    def test_wait_for_sync(self):
        mysync = 'fakesync'
        conn = self.fake_ecom_connection()

        self.driver.utils._is_sync_complete = mock.Mock(
            return_value = True)
        rc = self.driver.utils.wait_for_sync(conn, mysync)
        self.assertIsNone(rc)
        self.driver.utils._is_sync_complete.assert_called_once_with(
            conn, mysync)
        self.assertEqual(
            True,
            self.driver.utils._is_sync_complete.return_value)
        self.driver.utils._is_sync_complete.reset_mock()

        # Save the original state and restore it after this test
        loopingcall_orig = loopingcall.FixedIntervalLoopingCall
        loopingcall.FixedIntervalLoopingCall = mock.Mock()
        rc = self.driver.utils.wait_for_sync(conn, mysync)
        self.assertIsNone(rc)
        loopingcall.FixedIntervalLoopingCall.assert_called_once_with(
            mock.ANY)
        loopingcall.FixedIntervalLoopingCall.reset_mock()
        loopingcall.FixedIntervalLoopingCall = loopingcall_orig

    # Bug 1395830: _find_lun throws exception when lun is not found.
    def test_find_lun(self):
        keybindings = {'CreationClassName': u'Symm_StorageVolume',
                       'SystemName': u'SYMMETRIX+000195900551',
                       'DeviceID': u'1',
                       'SystemCreationClassName': u'Symm_StorageSystem'}
        provider_location = {'classname': 'Symm_StorageVolume',
                             'keybindings': keybindings}
        volume = EMC_StorageVolume()
        volume['name'] = 'vol1'
        volume['provider_location'] = six.text_type(provider_location)

        self.driver.common.conn = self.driver.common._get_ecom_connection()
        findlun = self.driver.common._find_lun(volume)
        getinstance = self.driver.common.conn._getinstance_storagevolume(
            keybindings)
        # Found lun.
        self.assertEqual(getinstance, findlun)

        keybindings2 = {'CreationClassName': u'Symm_StorageVolume',
                        'SystemName': u'SYMMETRIX+000195900551',
                        'DeviceID': u'9',
                        'SystemCreationClassName': u'Symm_StorageSystem'}
        provider_location2 = {'classname': 'Symm_StorageVolume',
                              'keybindings': keybindings2}
        volume2 = EMC_StorageVolume()
        volume2['name'] = 'myVol'
        volume2['provider_location'] = six.text_type(provider_location2)
        verify_orig = self.driver.common.utils.get_existing_instance
        self.driver.common.utils.get_existing_instance = mock.Mock(
            return_value=None)
        findlun2 = self.driver.common._find_lun(volume2)
        # Not found.
        self.assertIsNone(findlun2)
        instancename2 = self.driver.utils.get_instance_name(
            provider_location2['classname'],
            keybindings2)
        self.driver.common.utils.get_existing_instance.assert_called_once_with(
            self.driver.common.conn, instancename2)
        self.driver.common.utils.get_existing_instance.reset_mock()
        self.driver.common.utils.get_existing_instance = verify_orig

        keybindings3 = {'CreationClassName': u'Symm_StorageVolume',
                        'SystemName': u'SYMMETRIX+000195900551',
                        'DeviceID': u'9999',
                        'SystemCreationClassName': u'Symm_StorageSystem'}
        provider_location3 = {'classname': 'Symm_StorageVolume',
                              'keybindings': keybindings3}
        instancename3 = self.driver.utils.get_instance_name(
            provider_location3['classname'],
            keybindings3)
        # Error other than not found.
        arg = 9999, "test_error"
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.common.utils.process_exception_args,
                          arg, instancename3)

    # Bug 1393555 - masking view has been deleted by another process.
    def test_find_maskingview(self):
        conn = self.fake_ecom_connection()
        foundMaskingViewInstanceName = (
            self.driver.common.masking._find_masking_view(
                conn, self.data.lunmaskctrl_name, self.data.storage_system))
        # The masking view has been found.
        self.assertEqual(
            self.data.lunmaskctrl_name,
            conn.GetInstance(foundMaskingViewInstanceName)['ElementName'])

        self.driver.common.masking.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundMaskingViewInstanceName2 = (
            self.driver.common.masking._find_masking_view(
                conn, self.data.lunmaskctrl_name, self.data.storage_system))
        # The masking view has not been found.
        self.assertIsNone(foundMaskingViewInstanceName2)

    # Bug 1393555 - port group has been deleted by another process.
    def test_find_portgroup(self):
        conn = self.fake_ecom_connection()
        controllerConfigService = (
            self.driver.utils.find_controller_configuration_service(
                conn, self.data.storage_system))

        foundPortGroupInstanceName = (
            self.driver.common.masking._find_port_group(
                conn, controllerConfigService, self.data.port_group))
        # The port group has been found.
        self.assertEqual(
            self.data.port_group,
            conn.GetInstance(foundPortGroupInstanceName)['ElementName'])

        self.driver.common.masking.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundPortGroupInstanceName2 = (
            self.driver.common.masking._find_port_group(
                conn, controllerConfigService, self.data.port_group))
        # The port group has not been found as it has been deleted
        # externally or by another thread.
        self.assertIsNone(foundPortGroupInstanceName2)

    # Bug 1393555 - storage group has been deleted by another process.
    def test_get_storage_group_from_masking_view(self):
        conn = self.fake_ecom_connection()
        foundStorageGroupInstanceName = (
            self.driver.common.masking._get_storage_group_from_masking_view(
                conn, self.data.lunmaskctrl_name, self.data.storage_system))
        # The storage group has been found.
        self.assertEqual(
            self.data.storagegroupname,
            conn.GetInstance(foundStorageGroupInstanceName)['ElementName'])

        self.driver.common.masking.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundStorageGroupInstanceName2 = (
            self.driver.common.masking._get_storage_group_from_masking_view(
                conn, self.data.lunmaskctrl_name, self.data.storage_system))
        # The storage group has not been found as it has been deleted
        # externally or by another thread.
        self.assertIsNone(foundStorageGroupInstanceName2)

    # Bug 1393555 - initiator group has been deleted by another process.
    def test_get_initiator_group_from_masking_view(self):
        conn = self.fake_ecom_connection()
        foundInitiatorGroupInstanceName = (
            self.driver.common.masking._get_initiator_group_from_masking_view(
                conn, self.data.lunmaskctrl_name, self.data.storage_system))
        # The initiator group has been found.
        self.assertEqual(
            self.data.initiatorgroup_name,
            conn.GetInstance(foundInitiatorGroupInstanceName)['ElementName'])

        self.driver.common.masking.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundInitiatorGroupInstanceName2 = (
            self.driver.common.masking._get_storage_group_from_masking_view(
                conn, self.data.lunmaskctrl_name, self.data.storage_system))
        # The initiator group has not been found as it has been deleted
        # externally or by another thread.
        self.assertIsNone(foundInitiatorGroupInstanceName2)

    # Bug 1393555 - port group has been deleted by another process.
    def test_get_port_group_from_masking_view(self):
        conn = self.fake_ecom_connection()
        foundPortGroupInstanceName = (
            self.driver.common.masking._get_port_group_from_masking_view(
                conn, self.data.lunmaskctrl_name, self.data.storage_system))
        # The port group has been found.
        self.assertEqual(
            self.data.port_group,
            conn.GetInstance(foundPortGroupInstanceName)['ElementName'])

        self.driver.common.masking.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundPortGroupInstanceName2 = (
            self.driver.common.masking._get_port_group_from_masking_view(
                conn, self.data.lunmaskctrl_name, self.data.storage_system))
        # The port group has not been found as it has been deleted
        # externally or by another thread.
        self.assertIsNone(foundPortGroupInstanceName2)

    # Bug 1393555 - initiator group has been deleted by another process.
    def test_find_initiator_group(self):
        conn = self.fake_ecom_connection()
        controllerConfigService = (
            self.driver.utils.find_controller_configuration_service(
                conn, self.data.storage_system))

        foundInitiatorGroupInstanceName = (
            self.driver.common.masking._find_initiator_masking_group(
                conn, controllerConfigService, self.data.initiatorNames))
        # The initiator group has been found.
        self.assertEqual(
            self.data.initiatorgroup_name,
            conn.GetInstance(foundInitiatorGroupInstanceName)['ElementName'])

        self.driver.common.masking.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundInitiatorGroupInstanceName2 = (
            self.driver.common.masking._find_initiator_masking_group(
                conn, controllerConfigService, self.data.initiatorNames))
        # The initiator group has not been found as it has been deleted
        # externally or by another thread.
        self.assertIsNone(foundInitiatorGroupInstanceName2)

    # Bug 1393555 - hardware id has been deleted by another process.
    def test_get_storage_hardware_id_instance_names(self):
        conn = self.fake_ecom_connection()
        foundHardwareIdInstanceNames = (
            self.driver.common.masking._get_storage_hardware_id_instance_names(
                conn, self.data.initiatorNames, self.data.storage_system))
        # The hardware id list has been found.
        self.assertEqual(
            '123456789012345',
            conn.GetInstance(
                foundHardwareIdInstanceNames[0])['StorageID'])

        self.driver.common.masking.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundHardwareIdInstanceNames2 = (
            self.driver.common.masking._get_storage_hardware_id_instance_names(
                conn, self.data.initiatorNames, self.data.storage_system))
        # The hardware id list has not been found as it has been removed
        # externally.
        self.assertTrue(len(foundHardwareIdInstanceNames2) == 0)

    # Bug 1393555 - controller has been deleted by another process.
    def test_find_lunmasking_scsi_protocol_controller(self):
        self.driver.common.conn = self.fake_ecom_connection()
        foundControllerInstanceName = (
            self.driver.common._find_lunmasking_scsi_protocol_controller(
                self.data.storage_system, self.data.connector))
        # The controller has been found.
        self.assertEqual(
            'OS-fakehost-gold-MV',
            self.driver.common.conn.GetInstance(
                foundControllerInstanceName)['ElementName'])

        self.driver.common.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundControllerInstanceName2 = (
            self.driver.common._find_lunmasking_scsi_protocol_controller(
                self.data.storage_system, self.data.connector))
        # The controller has not been found as it has been removed
        # externally.
        self.assertIsNone(foundControllerInstanceName2)

    # Bug 1393555 - storage group has been deleted by another process.
    def test_get_policy_default_storage_group(self):
        conn = self.fake_ecom_connection()
        controllerConfigService = (
            self.driver.utils.find_controller_configuration_service(
                conn, self.data.storage_system))

        foundStorageMaskingGroupInstanceName = (
            self.driver.common.fast.get_policy_default_storage_group(
                conn, controllerConfigService, 'OS_default'))
        # The storage group has been found.
        self.assertEqual(
            'OS_default_GOLD1_SG',
            conn.GetInstance(
                foundStorageMaskingGroupInstanceName)['ElementName'])

        self.driver.common.fast.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundStorageMaskingGroupInstanceName2 = (
            self.driver.common.fast.get_policy_default_storage_group(
                conn, controllerConfigService, 'OS_default'))
        # The storage group has not been found as it has been removed
        # externally.
        self.assertIsNone(foundStorageMaskingGroupInstanceName2)

    # Bug 1393555 - policy has been deleted by another process.
    def test_get_capacities_associated_to_policy(self):
        conn = self.fake_ecom_connection()
        total_capacity_gb, free_capacity_gb = (
            self.driver.common.fast.get_capacities_associated_to_policy(
                conn, self.data.storage_system, self.data.policyrule))
        # The capacities associated to the policy have been found.
        self.assertEqual(self.data.totalmanagedspace_gbs, total_capacity_gb)
        self.assertEqual(self.data.subscribedcapacity_gbs, free_capacity_gb)

        self.driver.common.fast.utils.get_existing_instance = mock.Mock(
            return_value=None)
        total_capacity_gb_2, free_capacity_gb_2 = (
            self.driver.common.fast.get_capacities_associated_to_policy(
                conn, self.data.storage_system, self.data.policyrule))
        # The capacities have not been found as the policy has been
        # removed externally.
        self.assertEqual(0, total_capacity_gb_2)
        self.assertEqual(0, free_capacity_gb_2)

    # Bug 1393555 - storage group has been deleted by another process.
    def test_find_storage_masking_group(self):
        conn = self.fake_ecom_connection()
        controllerConfigService = (
            self.driver.utils.find_controller_configuration_service(
                conn, self.data.storage_system))

        foundStorageMaskingGroupInstanceName = (
            self.driver.common.utils.find_storage_masking_group(
                conn, controllerConfigService, self.data.storagegroupname))
        # The storage group has been found.
        self.assertEqual(
            self.data.storagegroupname,
            conn.GetInstance(
                foundStorageMaskingGroupInstanceName)['ElementName'])

        self.driver.common.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundStorageMaskingGroupInstanceName2 = (
            self.driver.common.utils.find_storage_masking_group(
                conn, controllerConfigService, self.data.storagegroupname))
        # The storage group has not been found as it has been removed
        # externally.
        self.assertIsNone(foundStorageMaskingGroupInstanceName2)

    # Bug 1393555 - pool has been deleted by another process.
    def test_get_pool_by_name(self):
        conn = self.fake_ecom_connection()

        foundPoolInstanceName = self.driver.common.utils.get_pool_by_name(
            conn, self.data.poolname, self.data.storage_system)
        # The pool has been found.
        self.assertEqual(
            self.data.poolname,
            conn.GetInstance(foundPoolInstanceName)['ElementName'])

        self.driver.common.utils.get_existing_instance = mock.Mock(
            return_value=None)
        foundPoolInstanceName2 = self.driver.common.utils.get_pool_by_name(
            conn, self.data.poolname, self.data.storage_system)
        # The pool has not been found as it has been removed externally.
        self.assertIsNone(foundPoolInstanceName2)

    def test_get_volume_stats_1364232(self):
        self.create_fake_config_file_1364232()
        self.assertEqual('000198700439',
                         self.driver.utils.parse_array_name_from_file(
                             self.config_file_1364232))
        self.assertEqual('FC_SLVR1',
                         self.driver.utils.parse_pool_name_from_file(
                             self.config_file_1364232))
        self.assertEqual('SILVER1',
                         self.driver.utils.parse_fast_policy_name_from_file(
                             self.config_file_1364232))
        self.assertIn('OS-PORTGROUP',
                      self.driver.utils.parse_file_to_get_port_group_name(
                          self.config_file_1364232))
        bExists = os.path.exists(self.config_file_1364232)
        if bExists:
            os.remove(self.config_file_1364232)

    @mock.patch.object(
        EMCVMAXUtils,
        'find_storageSystem',
        return_value=None)
    @mock.patch.object(
        EMCVMAXFast,
        'is_tiering_policy_enabled',
        return_value=False)
    @mock.patch.object(
        EMCVMAXUtils,
        'get_pool_capacities',
        return_value=(1234, 1200))
    @mock.patch.object(
        EMCVMAXUtils,
        'parse_array_name_from_file',
        return_value="123456789")
    def test_get_volume_stats_no_fast(self, mock_storage_system,
                                      mock_is_fast_enabled,
                                      mock_capacity, mock_array):
        self.driver.get_volume_stats(True)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_volume_no_fast_success(
            self, _mock_volume_type, mock_storage_system):
        self.driver.create_volume(self.data.test_volume_v2)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'storagetype: stripedmetacount': '4',
                      'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_volume_no_fast_striped_success(
            self, _mock_volume_type, mock_storage_system):
        self.driver.create_volume(self.data.test_volume_v2)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_delete_volume_no_fast_success(
            self, _mock_volume_type, mock_storage_system):
        self.driver.delete_volume(self.data.test_volume)

    def test_create_volume_no_fast_failed(self):
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume,
                          self.data.test_failed_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    def test_delete_volume_no_fast_notfound(self, _mock_volume_type):
        notfound_delete_vol = {}
        notfound_delete_vol['name'] = 'notfound_delete_vol'
        notfound_delete_vol['id'] = '10'
        notfound_delete_vol['CreationClassName'] = 'Symmm_StorageVolume'
        notfound_delete_vol['SystemName'] = self.data.storage_system
        notfound_delete_vol['DeviceID'] = notfound_delete_vol['id']
        notfound_delete_vol['SystemCreationClassName'] = 'Symm_StorageSystem'
        notfound_delete_vol['volume_type_id'] = 'abc'
        notfound_delete_vol['provider_location'] = None
        name = {}
        name['classname'] = 'Symm_StorageVolume'
        keys = {}
        keys['CreationClassName'] = notfound_delete_vol['CreationClassName']
        keys['SystemName'] = notfound_delete_vol['SystemName']
        keys['DeviceID'] = notfound_delete_vol['DeviceID']
        keys['SystemCreationClassName'] =\
            notfound_delete_vol['SystemCreationClassName']
        name['keybindings'] = keys

        self.driver.delete_volume(notfound_delete_vol)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_delete_volume_failed(
            self, _mock_volume_type, mock_storage_system):
        self.driver.create_volume(self.data.failed_delete_vol)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.delete_volume,
                          self.data.failed_delete_vol)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_wrap_find_device_number',
        return_value={'storagesystem': EMCVMAXCommonData.storage_system})
    @mock.patch.object(
        EMCVMAXUtils,
        'find_storage_masking_group',
        return_value='value')
    @mock.patch.object(
        EMCVMAXMasking,
        '_check_adding_volume_to_storage_group',
        return_value=None)
    def test_map_new_masking_view_no_fast_success(self,
                                                  mock_check,
                                                  mock_storage_group,
                                                  mock_wrap_device,
                                                  mock_wrap_group,
                                                  mock_volume_type):
        self.driver.initialize_connection(self.data.test_volume,
                                          self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_wrap_find_device_number',
        return_value={'hostlunid': 1,
                      'storagesystem': EMCVMAXCommonData.storage_system})
    @mock.patch.object(
        EMCVMAXUtils,
        'find_storage_masking_group',
        return_value='value')
    @mock.patch.object(
        EMCVMAXCommon,
        '_is_same_host',
        return_value=False)
    @mock.patch.object(
        EMCVMAXMasking,
        '_check_adding_volume_to_storage_group',
        return_value=None)
    def test_map_live_migration_no_fast_success(self,
                                                mock_check,
                                                mock_same_host,
                                                mock_storage_group,
                                                mock_wrap_device,
                                                mock_wrap_group,
                                                mock_volume_type):
        self.driver.initialize_connection(self.data.test_volume,
                                          self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_wrap_find_device_number',
        return_value={'hostlunid': 1,
                      'storagesystem': EMCVMAXCommonData.storage_system})
    @mock.patch.object(
        EMCVMAXCommon,
        '_is_same_host',
        return_value=True)
    def test_already_mapped_no_fast_success(self,
                                            mock_same_host,
                                            mock_wrap_device,
                                            mock_wrap_group,
                                            mock_volume_type):
        self.driver.initialize_connection(self.data.test_volume,
                                          self.data.connector)

    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_wrap_find_device_number',
        return_value={'storagesystem': EMCVMAXCommonData.storage_system})
    def test_map_no_fast_failed(self, mock_wrap_group, mock_wrap_device):
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.initialize_connection,
                          self.data.test_volume,
                          self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'find_storage_masking_group',
        return_value=EMCVMAXCommonData.storagegroupname)
    def test_detach_no_fast_success(self, mock_volume_type,
                                    mock_storage_group):

        self.driver.terminate_connection(
            self.data.test_volume, self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXUtils, 'find_storage_system',
        return_value={'Name': EMCVMAXCommonData.storage_system})
    @mock.patch.object(
        EMCVMAXUtils,
        'find_storage_masking_group',
        return_value=EMCVMAXCommonData.storagegroupname)
    def test_detach_no_fast_last_volume_success(
            self, mock_volume_type,
            mock_storage_system, mock_storage_group):
        self.driver.terminate_connection(
            self.data.test_volume, self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_size',
        return_value='2147483648')
    def test_extend_volume_no_fast_success(
            self, _mock_volume_type, mock_volume_size):
        newSize = '2'
        self.driver.extend_volume(self.data.test_volume, newSize)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'storagetype: stripedmetacount': '4',
                      'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'check_if_volume_is_extendable',
        return_value='False')
    def test_extend_volume_striped_no_fast_failed(
            self, _mock_volume_type, _mock_is_extendable):
        newSize = '2'
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.extend_volume,
                          self.data.test_volume,
                          newSize)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        FakeDB,
        'volume_get',
        return_value=EMCVMAXCommonData.test_source_volume)
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=[EMCVMAXCommonData.test_volume])
    @mock.patch.object(
        EMCVMAXUtils,
        'get_meta_members_capacity_in_bit',
        return_value=[1234567, 7654321])
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_snapshot_different_sizes_meta_no_fast_success(
            self, mock_volume_type, mock_volume,
            mock_meta, mock_size, mock_pool):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        common = self.driver.common
        volumeDict = {'classname': u'Symm_StorageVolume',
                      'keybindings': EMCVMAXCommonData.keybindings}
        common.provision.create_volume_from_pool = (
            mock.Mock(return_value=(volumeDict, 0L)))
        common.provision.get_volume_dict_from_job = (
            mock.Mock(return_value=volumeDict))
        self.driver.create_snapshot(self.data.test_volume)

    def test_create_snapshot_no_fast_failed(self):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_snapshot,
                          self.data.test_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_find_storage_sync_sv_sv',
        return_value=(None, None))
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=[EMCVMAXCommonData.test_volume])
    @mock.patch.object(
        EMCVMAXUtils,
        'get_meta_members_capacity_in_bit',
        return_value=[1234567])
    def test_create_volume_from_same_size_meta_snapshot(
            self, mock_volume_type, mock_sync_sv, mock_meta, mock_size):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.driver.create_volume_from_snapshot(
            self.data.test_volume, self.data.test_volume)

    def test_create_volume_from_snapshot_no_fast_failed(self):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume_from_snapshot,
                          self.data.test_volume,
                          EMCVMAXCommonData.test_source_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        FakeDB,
        'volume_get',
        return_value=EMCVMAXCommonData.test_source_volume)
    @mock.patch.object(
        EMCVMAXCommon,
        '_find_storage_sync_sv_sv',
        return_value=(None, None))
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=None)
    def test_create_clone_simple_volume_no_fast_success(
            self, mock_volume_type, mock_volume, mock_sync_sv,
            mock_simple_volume):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.driver.create_cloned_volume(self.data.test_volume,
                                         EMCVMAXCommonData.test_source_volume)

    def test_create_clone_no_fast_failed(self):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_cloned_volume,
                          self.data.test_volume,
                          EMCVMAXCommonData.test_source_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    def test_migrate_volume_no_fast_success(self, _mock_volume_type):
        self.driver.migrate_volume(self.data.test_ctxt, self.data.test_volume,
                                   self.data.test_host)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSINoFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'parse_pool_instance_id',
        return_value=('silver', 'SYMMETRIX+000195900551'))
    def test_retype_volume_no_fast_success(
            self, _mock_volume_type, mock_values):
        self.driver.retype(
            self.data.test_ctxt, self.data.test_volume, self.data.new_type,
            self.data.diff, self.data.test_host)

    def test_check_for_setup_error(self):
        self.driver.configuration.iscsi_ip_address = '1.1.1.1'
        self.driver.check_for_setup_error()
        self.driver.configuration.iscsi_ip_address = None
        self.assertRaises(exception.InvalidInput,
                          self.driver.check_for_setup_error)

    def _cleanup(self):
        bExists = os.path.exists(self.config_file_path)
        if bExists:
            os.remove(self.config_file_path)
        shutil.rmtree(self.tempdir)


class EMCVMAXISCSIDriverFastTestCase(test.TestCase):

    def setUp(self):

        self.data = EMCVMAXCommonData()

        self.tempdir = tempfile.mkdtemp()
        super(EMCVMAXISCSIDriverFastTestCase, self).setUp()
        self.config_file_path = None
        self.create_fake_config_file_fast()
        self.addCleanup(self._cleanup)

        configuration = mock.Mock()
        configuration.cinder_emc_config_file = self.config_file_path
        configuration.safe_get.return_value = 'ISCSIFAST'
        configuration.config_group = 'ISCSIFAST'

        self.stubs.Set(EMCVMAXISCSIDriver, 'smis_do_iscsi_discovery',
                       self.fake_do_iscsi_discovery)
        self.stubs.Set(EMCVMAXCommon, '_get_ecom_connection',
                       self.fake_ecom_connection)
        instancename = FakeCIMInstanceName()
        self.stubs.Set(EMCVMAXUtils, 'get_instance_name',
                       instancename.fake_getinstancename)
        self.stubs.Set(time, 'sleep',
                       self.fake_sleep)
        driver = EMCVMAXISCSIDriver(configuration=configuration)
        driver.db = FakeDB()
        self.driver = driver

    def create_fake_config_file_fast(self):

        doc = Document()
        emc = doc.createElement("EMC")
        doc.appendChild(emc)

        array = doc.createElement("Array")
        arraytext = doc.createTextNode("1234567891011")
        emc.appendChild(array)
        array.appendChild(arraytext)

        fastPolicy = doc.createElement("FastPolicy")
        fastPolicyText = doc.createTextNode("GOLD1")
        emc.appendChild(fastPolicy)
        fastPolicy.appendChild(fastPolicyText)

        ecomserverip = doc.createElement("EcomServerIp")
        ecomserveriptext = doc.createTextNode("1.1.1.1")
        emc.appendChild(ecomserverip)
        ecomserverip.appendChild(ecomserveriptext)

        ecomserverport = doc.createElement("EcomServerPort")
        ecomserverporttext = doc.createTextNode("10")
        emc.appendChild(ecomserverport)
        ecomserverport.appendChild(ecomserverporttext)

        ecomusername = doc.createElement("EcomUserName")
        ecomusernametext = doc.createTextNode("user")
        emc.appendChild(ecomusername)
        ecomusername.appendChild(ecomusernametext)

        ecompassword = doc.createElement("EcomPassword")
        ecompasswordtext = doc.createTextNode("pass")
        emc.appendChild(ecompassword)
        ecompassword.appendChild(ecompasswordtext)

        timeout = doc.createElement("Timeout")
        timeouttext = doc.createTextNode("0")
        emc.appendChild(timeout)
        timeout.appendChild(timeouttext)

        portgroup = doc.createElement("PortGroup")
        portgrouptext = doc.createTextNode(self.data.port_group)
        portgroup.appendChild(portgrouptext)

        pool = doc.createElement("Pool")
        pooltext = doc.createTextNode("gold")
        emc.appendChild(pool)
        pool.appendChild(pooltext)

        array = doc.createElement("Array")
        arraytext = doc.createTextNode("0123456789")
        emc.appendChild(array)
        array.appendChild(arraytext)

        portgroups = doc.createElement("PortGroups")
        portgroups.appendChild(portgroup)
        emc.appendChild(portgroups)

        filename = 'cinder_emc_config_ISCSIFAST.xml'

        self.config_file_path = self.tempdir + '/' + filename

        f = open(self.config_file_path, 'w')
        doc.writexml(f)
        f.close()

    def fake_ecom_connection(self):
        conn = FakeEcomConnection()
        return conn

    def fake_do_iscsi_discovery(self, volume):
        output = []
        item = '10.10.0.50: 3260,1 iqn.1992-04.com.emc: 50000973f006dd80'
        output.append(item)
        return output

    def fake_sleep(self, seconds):
        return

    @mock.patch.object(
        EMCVMAXUtils,
        'find_storageSystem',
        return_value=None)
    @mock.patch.object(
        EMCVMAXFast,
        'is_tiering_policy_enabled',
        return_value=True)
    @mock.patch.object(
        EMCVMAXFast,
        'get_tier_policy_by_name',
        return_value=None)
    @mock.patch.object(
        EMCVMAXFast,
        'get_capacities_associated_to_policy',
        return_value=(1234, 1200))
    @mock.patch.object(
        EMCVMAXUtils,
        'parse_array_name_from_file',
        return_value="123456789")
    def test_get_volume_stats_fast(self, mock_storage_system,
                                   mock_is_fast_enabled,
                                   mock_get_policy, mock_capacity, mock_array):
        self.driver.get_volume_stats(True)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    def test_create_volume_fast_success(
            self, _mock_volume_type, mock_storage_system, mock_pool_policy):
        self.driver.create_volume(self.data.test_volume_v2)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'storagetype: stripedmetacount': '4',
                      'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    def test_create_volume_fast_striped_success(
            self, _mock_volume_type, mock_storage_system, mock_pool_policy):
        self.driver.create_volume(self.data.test_volume_v2)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    def test_delete_volume_fast_success(
            self, _mock_volume_type, mock_storage_group):
        self.driver.delete_volume(self.data.test_volume)

    def test_create_volume_fast_failed(self):
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume,
                          self.data.test_failed_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    def test_delete_volume_fast_notfound(
            self, _mock_volume_type, mock_wrapper):
        notfound_delete_vol = {}
        notfound_delete_vol['name'] = 'notfound_delete_vol'
        notfound_delete_vol['id'] = '10'
        notfound_delete_vol['CreationClassName'] = 'Symmm_StorageVolume'
        notfound_delete_vol['SystemName'] = self.data.storage_system
        notfound_delete_vol['DeviceID'] = notfound_delete_vol['id']
        notfound_delete_vol['SystemCreationClassName'] = 'Symm_StorageSystem'
        name = {}
        name['classname'] = 'Symm_StorageVolume'
        keys = {}
        keys['CreationClassName'] = notfound_delete_vol['CreationClassName']
        keys['SystemName'] = notfound_delete_vol['SystemName']
        keys['DeviceID'] = notfound_delete_vol['DeviceID']
        keys['SystemCreationClassName'] =\
            notfound_delete_vol['SystemCreationClassName']
        name['keybindings'] = keys
        notfound_delete_vol['volume_type_id'] = 'abc'
        notfound_delete_vol['provider_location'] = None
        self.driver.delete_volume(notfound_delete_vol)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    def test_delete_volume_fast_failed(
            self, _mock_volume_type, _mock_storage_group,
            mock_storage_system, mock_policy_pool):
        self.driver.create_volume(self.data.failed_delete_vol)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.delete_volume,
                          self.data.failed_delete_vol)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_wrap_find_device_number',
        return_value={'hostlunid': 1,
                      'storagesystem': EMCVMAXCommonData.storage_system})
    @mock.patch.object(
        EMCVMAXCommon,
        '_is_same_host',
        return_value=True)
    def test_map_fast_success(self, mock_same_host, mock_wrap_device,
                              mock_wrap_group, mock_volume_type):
        self.driver.initialize_connection(self.data.test_volume,
                                          self.data.connector)

    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_wrap_find_device_number',
        return_value={'storagesystem': EMCVMAXCommonData.storage_system})
    def test_map_fast_failed(self, mock_wrap_group, mock_wrap_device):
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.initialize_connection,
                          self.data.test_volume,
                          self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'find_storage_masking_group',
        return_value=EMCVMAXCommonData.storagegroupname)
    def test_detach_fast_success(self, mock_volume_type,
                                 mock_storage_group):

        self.driver.terminate_connection(
            self.data.test_volume, self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXUtils, 'find_storage_system',
        return_value={'Name': EMCVMAXCommonData.storage_system})
    @mock.patch.object(
        EMCVMAXUtils,
        'find_storage_masking_group',
        return_value=EMCVMAXCommonData.storagegroupname)
    def test_detach_fast_last_volume_success(
            self, mock_volume_type,
            mock_storage_system, mock_storage_group):
        self.driver.terminate_connection(
            self.data.test_volume, self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_size',
        return_value='2147483648')
    def test_extend_volume_fast_success(
            self, _mock_volume_type, mock_volume_size):
        newSize = '2'
        self.driver.extend_volume(self.data.test_volume, newSize)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'check_if_volume_is_extendable',
        return_value='False')
    def test_extend_volume_striped_fast_failed(
            self, _mock_volume_type, _mock_is_extendable):
        newSize = '2'
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.extend_volume,
                          self.data.test_volume,
                          newSize)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        FakeDB,
        'volume_get',
        return_value=EMCVMAXCommonData.test_source_volume)
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=[EMCVMAXCommonData.test_volume])
    @mock.patch.object(
        EMCVMAXUtils,
        'get_meta_members_capacity_in_bit',
        return_value=[1234567, 7654321])
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_snapshot_different_sizes_meta_fast_success(
            self, mock_volume_type, mock_volume, mock_meta,
            mock_size, mock_pool, mock_policy):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        common = self.driver.common

        volumeDict = {'classname': u'Symm_StorageVolume',
                      'keybindings': EMCVMAXCommonData.keybindings}
        common.provision.create_volume_from_pool = (
            mock.Mock(return_value=(volumeDict, 0L)))
        common.provision.get_volume_dict_from_job = (
            mock.Mock(return_value=volumeDict))
        common.fast.is_volume_in_default_SG = (
            mock.Mock(return_value=True))
        self.driver.create_snapshot(self.data.test_volume)

    def test_create_snapshot_fast_failed(self):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_snapshot,
                          self.data.test_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_find_storage_sync_sv_sv',
        return_value=(None, None))
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=[EMCVMAXCommonData.test_volume])
    @mock.patch.object(
        EMCVMAXUtils,
        'get_meta_members_capacity_in_bit',
        return_value=[1234567])
    def test_create_volume_from_same_size_meta_snapshot(
            self, mock_volume_type, mock_sync_sv, mock_meta, mock_size):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.driver.common.utils.find_storage_configuration_service = (
            mock.Mock(return_value=EMCVMAXCommonData.storage_system))
        self.driver.common._get_or_create_default_storage_group = (
            mock.Mock(return_value=EMCVMAXCommonData.default_storage_group))
        self.driver.common.fast.is_volume_in_default_SG = (
            mock.Mock(return_value=True))
        self.driver.create_volume_from_snapshot(
            self.data.test_volume, self.data.test_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST',
                      'FASTPOLICY': 'FC_GOLD1'})
    @mock.patch.object(
        EMCVMAXUtils,
        'find_replication_service',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_find_storage_sync_sv_sv',
        return_value=(None, None))
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=None)
    def test_create_volume_from_snapshot_fast_failed(
            self, mock_type, mock_rep_service, mock_sync_sv, mock_meta):

        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume_from_snapshot,
                          self.data.test_volume,
                          EMCVMAXCommonData.test_source_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        FakeDB,
        'volume_get',
        return_value=EMCVMAXCommonData.test_source_volume)
    @mock.patch.object(
        EMCVMAXCommon,
        '_find_storage_sync_sv_sv',
        return_value=(None, None))
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=None)
    def test_create_clone_simple_volume_fast_success(
            self, mock_volume_type, mock_volume, mock_sync_sv,
            mock_simple_volume):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.driver.common.utils.find_storage_configuration_service = (
            mock.Mock(return_value=EMCVMAXCommonData.storage_system))
        self.driver.common._get_or_create_default_storage_group = (
            mock.Mock(return_value=EMCVMAXCommonData.default_storage_group))
        self.driver.common.fast.is_volume_in_default_SG = (
            mock.Mock(return_value=True))
        self.driver.create_cloned_volume(self.data.test_volume,
                                         EMCVMAXCommonData.test_source_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        FakeDB,
        'volume_get',
        return_value=EMCVMAXCommonData.test_source_volume)
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=[EMCVMAXCommonData.test_volume])
    @mock.patch.object(
        EMCVMAXUtils,
        'get_meta_members_capacity_in_bit',
        return_value=[1234567, 7654321])
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_clone_fast_failed(
            self, mock_volume_type, mock_vol, mock_policy, mock_meta,
            mock_size, mock_pool):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.driver.common._modify_and_get_composite_volume_instance = (
            mock.Mock(return_value=(1L, None)))
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_cloned_volume,
                          self.data.test_volume,
                          EMCVMAXCommonData.test_source_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    def test_migrate_volume_fast_success(self, _mock_volume_type):
        self.driver.migrate_volume(self.data.test_ctxt, self.data.test_volume,
                                   self.data.test_host)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'ISCSIFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'parse_pool_instance_id',
        return_value=('silver', 'SYMMETRIX+000195900551'))
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    def test_retype_volume_fast_success(
            self, _mock_volume_type, mock_values, mock_wrap):
        self.driver.retype(
            self.data.test_ctxt, self.data.test_volume, self.data.new_type,
            self.data.diff, self.data.test_host)

    def _cleanup(self):
        bExists = os.path.exists(self.config_file_path)
        if bExists:
            os.remove(self.config_file_path)
        shutil.rmtree(self.tempdir)


class EMCVMAXFCDriverNoFastTestCase(test.TestCase):
    def setUp(self):

        self.data = EMCVMAXCommonData()

        self.tempdir = tempfile.mkdtemp()
        super(EMCVMAXFCDriverNoFastTestCase, self).setUp()
        self.config_file_path = None
        self.create_fake_config_file_no_fast()
        self.addCleanup(self._cleanup)

        configuration = mock.Mock()
        configuration.cinder_emc_config_file = self.config_file_path
        configuration.safe_get.return_value = 'FCNoFAST'
        configuration.config_group = 'FCNoFAST'

        self.stubs.Set(EMCVMAXCommon, '_get_ecom_connection',
                       self.fake_ecom_connection)
        instancename = FakeCIMInstanceName()
        self.stubs.Set(EMCVMAXUtils, 'get_instance_name',
                       instancename.fake_getinstancename)
        self.stubs.Set(time, 'sleep',
                       self.fake_sleep)

        driver = EMCVMAXFCDriver(configuration=configuration)
        driver.db = FakeDB()
        driver.common.conn = FakeEcomConnection()
        driver.zonemanager_lookup_service = FakeLookupService()
        self.driver = driver

    def create_fake_config_file_no_fast(self):

        doc = Document()
        emc = doc.createElement("EMC")
        doc.appendChild(emc)

        array = doc.createElement("Array")
        arraytext = doc.createTextNode("1234567891011")
        emc.appendChild(array)
        array.appendChild(arraytext)

        ecomserverip = doc.createElement("EcomServerIp")
        ecomserveriptext = doc.createTextNode("1.1.1.1")
        emc.appendChild(ecomserverip)
        ecomserverip.appendChild(ecomserveriptext)

        ecomserverport = doc.createElement("EcomServerPort")
        ecomserverporttext = doc.createTextNode("10")
        emc.appendChild(ecomserverport)
        ecomserverport.appendChild(ecomserverporttext)

        ecomusername = doc.createElement("EcomUserName")
        ecomusernametext = doc.createTextNode("user")
        emc.appendChild(ecomusername)
        ecomusername.appendChild(ecomusernametext)

        ecompassword = doc.createElement("EcomPassword")
        ecompasswordtext = doc.createTextNode("pass")
        emc.appendChild(ecompassword)
        ecompassword.appendChild(ecompasswordtext)

        portgroup = doc.createElement("PortGroup")
        portgrouptext = doc.createTextNode(self.data.port_group)
        portgroup.appendChild(portgrouptext)

        portgroups = doc.createElement("PortGroups")
        portgroups.appendChild(portgroup)
        emc.appendChild(portgroups)

        pool = doc.createElement("Pool")
        pooltext = doc.createTextNode("gold")
        emc.appendChild(pool)
        pool.appendChild(pooltext)

        array = doc.createElement("Array")
        arraytext = doc.createTextNode("0123456789")
        emc.appendChild(array)
        array.appendChild(arraytext)

        timeout = doc.createElement("Timeout")
        timeouttext = doc.createTextNode("0")
        emc.appendChild(timeout)
        timeout.appendChild(timeouttext)

        filename = 'cinder_emc_config_FCNoFAST.xml'

        self.config_file_path = self.tempdir + '/' + filename

        f = open(self.config_file_path, 'w')
        doc.writexml(f)
        f.close()

    def fake_ecom_connection(self):
        conn = FakeEcomConnection()
        return conn

    def fake_sleep(self, seconds):
        return

    @mock.patch.object(
        EMCVMAXUtils,
        'find_storageSystem',
        return_value=None)
    @mock.patch.object(
        EMCVMAXFast,
        'is_tiering_policy_enabled',
        return_value=False)
    @mock.patch.object(
        EMCVMAXUtils,
        'get_pool_capacities',
        return_value=(1234, 1200))
    @mock.patch.object(
        EMCVMAXUtils,
        'parse_array_name_from_file',
        return_value="123456789")
    def test_get_volume_stats_no_fast(self,
                                      mock_storage_system,
                                      mock_is_fast_enabled,
                                      mock_capacity,
                                      mock_array):
        self.driver.get_volume_stats(True)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_volume_no_fast_success(
            self, _mock_volume_type, mock_storage_system):
        self.driver.create_volume(self.data.test_volume_v2)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'storagetype: stripedmetacount': '4',
                      'volume_backend_name': 'FCNoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_volume_no_fast_striped_success(
            self, _mock_volume_type, mock_storage_system):
        self.driver.create_volume(self.data.test_volume_v2)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_delete_volume_no_fast_success(
            self, _mock_volume_type, mock_storage_system):
        self.driver.delete_volume(self.data.test_volume)

    def test_create_volume_no_fast_failed(self):
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume,
                          self.data.test_failed_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    def test_delete_volume_no_fast_notfound(self, _mock_volume_type):
        notfound_delete_vol = {}
        notfound_delete_vol['name'] = 'notfound_delete_vol'
        notfound_delete_vol['id'] = '10'
        notfound_delete_vol['CreationClassName'] = 'Symmm_StorageVolume'
        notfound_delete_vol['SystemName'] = self.data.storage_system
        notfound_delete_vol['DeviceID'] = notfound_delete_vol['id']
        notfound_delete_vol['SystemCreationClassName'] = 'Symm_StorageSystem'
        name = {}
        name['classname'] = 'Symm_StorageVolume'
        keys = {}
        keys['CreationClassName'] = notfound_delete_vol['CreationClassName']
        keys['SystemName'] = notfound_delete_vol['SystemName']
        keys['DeviceID'] = notfound_delete_vol['DeviceID']
        keys['SystemCreationClassName'] =\
            notfound_delete_vol['SystemCreationClassName']
        name['keybindings'] = keys
        notfound_delete_vol['volume_type_id'] = 'abc'
        notfound_delete_vol['provider_location'] = None
        self.driver.delete_volume(notfound_delete_vol)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_delete_volume_failed(
            self, _mock_volume_type, mock_storage_system):
        self.driver.create_volume(self.data.failed_delete_vol)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.delete_volume,
                          self.data.failed_delete_vol)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST',
                      'FASTPOLICY': 'FC_GOLD1'})
    @mock.patch.object(
        EMCVMAXMasking,
        'get_masking_view_from_storage_group',
        return_value=EMCVMAXCommonData.lunmaskctrl_name)
    @mock.patch.object(
        EMCVMAXProvision,
        '_find_new_storage_group',
        return_value='Any')
    @mock.patch.object(
        EMCVMAXMasking,
        '_check_adding_volume_to_storage_group',
        return_value=None)
    def test_map_lookup_service_no_fast_success(
            self, mock_add_check, mock_new_sg,
            mock_maskingview, mock_volume_type):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        common = self.driver.common
        common.get_target_wwns_from_masking_view = mock.Mock(
            return_value=EMCVMAXCommonData.target_wwns)
        lookup_service = self.driver.zonemanager_lookup_service
        lookup_service.get_device_mapping_from_network = mock.Mock(
            return_value=EMCVMAXCommonData.device_map)
        data = self.driver.initialize_connection(self.data.test_volume,
                                                 self.data.connector)
        common.get_target_wwns_from_masking_view.assert_called_once_with(
            EMCVMAXCommonData.storage_system, self.data.test_volume,
            EMCVMAXCommonData.connector)
        lookup_service.get_device_mapping_from_network.assert_called_once_with(
            EMCVMAXCommonData.connector['wwpns'],
            EMCVMAXCommonData.target_wwns)

        # Test the lookup service code path.
        for init, target in data['data']['initiator_target_map'].items():
            self.assertEqual(init, target[0][::-1])

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST',
                      'FASTPOLICY': 'FC_GOLD1'})
    @mock.patch.object(
        EMCVMAXCommon,
        'find_device_number',
        return_value={'Name': "0001"})
    def test_map_no_fast_failed(self, mock_wrap_group, mock_maskingview):
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.initialize_connection,
                          self.data.test_volume,
                          self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST',
                      'FASTPOLICY': 'FC_GOLD1'})
    @mock.patch.object(
        EMCVMAXMasking,
        'get_masking_view_by_volume',
        return_value=EMCVMAXCommonData.lunmaskctrl_name)
    def test_detach_no_fast_success(self, mock_volume_type, mock_maskingview):
        self.driver.terminate_connection(self.data.test_volume,
                                         self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        'get_masking_view_by_volume',
        return_value=EMCVMAXCommonData.lunmaskctrl_name)
    def test_detach_no_fast_last_volume_success(
            self, mock_volume_type, mock_mv):
        self.driver.terminate_connection(self.data.test_source_volume,
                                         self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_size',
        return_value='2147483648')
    def test_extend_volume_no_fast_success(self, _mock_volume_type,
                                           _mock_volume_size):
        newSize = '2'
        self.driver.extend_volume(self.data.test_volume, newSize)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'check_if_volume_is_extendable',
        return_value='False')
    def test_extend_volume_striped_no_fast_failed(
            self, _mock_volume_type, _mock_is_extendable):
        newSize = '2'
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.extend_volume,
                          self.data.test_volume,
                          newSize)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    def test_migrate_volume_no_fast_success(self, _mock_volume_type):
        self.driver.migrate_volume(self.data.test_ctxt, self.data.test_volume,
                                   self.data.test_host)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCNoFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'parse_pool_instance_id',
        return_value=('silver', 'SYMMETRIX+000195900551'))
    def test_retype_volume_no_fast_success(
            self, _mock_volume_type, mock_values):
        self.driver.retype(
            self.data.test_ctxt, self.data.test_volume, self.data.new_type,
            self.data.diff, self.data.test_host)

    def _cleanup(self):
        bExists = os.path.exists(self.config_file_path)
        if bExists:
            os.remove(self.config_file_path)
        shutil.rmtree(self.tempdir)


class EMCVMAXFCDriverFastTestCase(test.TestCase):

    def setUp(self):

        self.data = EMCVMAXCommonData()

        self.tempdir = tempfile.mkdtemp()
        super(EMCVMAXFCDriverFastTestCase, self).setUp()
        self.config_file_path = None
        self.create_fake_config_file_fast()
        self.addCleanup(self._cleanup)

        configuration = mock.Mock()
        configuration.cinder_emc_config_file = self.config_file_path
        configuration.safe_get.return_value = 'FCFAST'
        configuration.config_group = 'FCFAST'

        self.stubs.Set(EMCVMAXCommon, '_get_ecom_connection',
                       self.fake_ecom_connection)
        instancename = FakeCIMInstanceName()
        self.stubs.Set(EMCVMAXUtils, 'get_instance_name',
                       instancename.fake_getinstancename)
        self.stubs.Set(time, 'sleep',
                       self.fake_sleep)

        driver = EMCVMAXFCDriver(configuration=configuration)
        driver.db = FakeDB()
        driver.common.conn = FakeEcomConnection()
        driver.zonemanager_lookup_service = None
        self.driver = driver

    def create_fake_config_file_fast(self):

        doc = Document()
        emc = doc.createElement("EMC")
        doc.appendChild(emc)

        fastPolicy = doc.createElement("FastPolicy")
        fastPolicyText = doc.createTextNode("GOLD1")
        emc.appendChild(fastPolicy)
        fastPolicy.appendChild(fastPolicyText)

        ecomserverip = doc.createElement("EcomServerIp")
        ecomserveriptext = doc.createTextNode("1.1.1.1")
        emc.appendChild(ecomserverip)
        ecomserverip.appendChild(ecomserveriptext)

        ecomserverport = doc.createElement("EcomServerPort")
        ecomserverporttext = doc.createTextNode("10")
        emc.appendChild(ecomserverport)
        ecomserverport.appendChild(ecomserverporttext)

        ecomusername = doc.createElement("EcomUserName")
        ecomusernametext = doc.createTextNode("user")
        emc.appendChild(ecomusername)
        ecomusername.appendChild(ecomusernametext)

        ecompassword = doc.createElement("EcomPassword")
        ecompasswordtext = doc.createTextNode("pass")
        emc.appendChild(ecompassword)
        ecompassword.appendChild(ecompasswordtext)

        portgroup = doc.createElement("PortGroup")
        portgrouptext = doc.createTextNode(self.data.port_group)
        portgroup.appendChild(portgrouptext)

        pool = doc.createElement("Pool")
        pooltext = doc.createTextNode("gold")
        emc.appendChild(pool)
        pool.appendChild(pooltext)

        array = doc.createElement("Array")
        arraytext = doc.createTextNode("0123456789")
        emc.appendChild(array)
        array.appendChild(arraytext)

        portgroups = doc.createElement("PortGroups")
        portgroups.appendChild(portgroup)
        emc.appendChild(portgroups)

        timeout = doc.createElement("Timeout")
        timeouttext = doc.createTextNode("0")
        emc.appendChild(timeout)
        timeout.appendChild(timeouttext)

        filename = 'cinder_emc_config_FCFAST.xml'

        self.config_file_path = self.tempdir + '/' + filename

        f = open(self.config_file_path, 'w')
        doc.writexml(f)
        f.close()

    def fake_ecom_connection(self):
        conn = FakeEcomConnection()
        return conn

    def fake_sleep(self, seconds):
        return

    @mock.patch.object(
        EMCVMAXUtils,
        'find_storageSystem',
        return_value=None)
    @mock.patch.object(
        EMCVMAXFast,
        'is_tiering_policy_enabled',
        return_value=True)
    @mock.patch.object(
        EMCVMAXFast,
        'get_tier_policy_by_name',
        return_value=None)
    @mock.patch.object(
        EMCVMAXFast,
        'get_capacities_associated_to_policy',
        return_value=(1234, 1200))
    @mock.patch.object(
        EMCVMAXUtils,
        'parse_array_name_from_file',
        return_value="123456789")
    def test_get_volume_stats_fast(self,
                                   mock_storage_system,
                                   mock_is_fast_enabled,
                                   mock_get_policy,
                                   mock_capacity,
                                   mock_array):
        self.driver.get_volume_stats(True)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    def test_create_volume_fast_success(
            self, _mock_volume_type, mock_storage_system, mock_pool_policy):
        self.driver.create_volume(self.data.test_volume_v2)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'storagetype: stripedmetacount': '4',
                      'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    def test_create_volume_fast_striped_success(
            self, _mock_volume_type, mock_storage_system, mock_pool_policy):
        self.driver.create_volume(self.data.test_volume_v2)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    def test_delete_volume_fast_success(self, _mock_volume_type,
                                        mock_storage_group):
        self.driver.delete_volume(self.data.test_volume)

    def test_create_volume_fast_failed(self):
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume,
                          self.data.test_failed_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    def test_delete_volume_fast_notfound(self, _mock_volume_type):
        """We do not set the provider location.
        """
        notfound_delete_vol = {}
        notfound_delete_vol['name'] = 'notfound_delete_vol'
        notfound_delete_vol['id'] = '10'
        notfound_delete_vol['CreationClassName'] = 'Symmm_StorageVolume'
        notfound_delete_vol['SystemName'] = self.data.storage_system
        notfound_delete_vol['DeviceID'] = notfound_delete_vol['id']
        notfound_delete_vol['SystemCreationClassName'] = 'Symm_StorageSystem'
        name = {}
        name['classname'] = 'Symm_StorageVolume'
        keys = {}
        keys['CreationClassName'] = notfound_delete_vol['CreationClassName']
        keys['SystemName'] = notfound_delete_vol['SystemName']
        keys['DeviceID'] = notfound_delete_vol['DeviceID']
        keys['SystemCreationClassName'] =\
            notfound_delete_vol['SystemCreationClassName']
        name['keybindings'] = keys
        notfound_delete_vol['volume_type_id'] = 'abc'
        notfound_delete_vol['provider_location'] = None

        self.driver.delete_volume(notfound_delete_vol)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    def test_delete_volume_fast_failed(
            self, _mock_volume_type, mock_wrapper,
            mock_storage_system, mock_pool_policy):
        self.driver.create_volume(self.data.failed_delete_vol)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.delete_volume,
                          self.data.failed_delete_vol)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST',
                      'FASTPOLICY': 'FC_GOLD1'})
    @mock.patch.object(
        EMCVMAXMasking,
        'get_masking_view_from_storage_group',
        return_value=EMCVMAXCommonData.lunmaskctrl_name)
    @mock.patch.object(
        EMCVMAXProvision,
        '_find_new_storage_group',
        return_value='Any')
    @mock.patch.object(
        EMCVMAXMasking,
        '_check_adding_volume_to_storage_group',
        return_value=None)
    def test_map_fast_success(self, mock_add_check, mock_new_sg,
                              mock_maskingview, mock_volume_type):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        common = self.driver.common
        common.get_target_wwns = mock.Mock(
            return_value=EMCVMAXCommonData.target_wwns)
        data = self.driver.initialize_connection(
            self.data.test_volume, self.data.connector)
        # Test the no lookup service, pre-zoned case.
        common.get_target_wwns.assert_called_once_with(
            EMCVMAXCommonData.storage_system, EMCVMAXCommonData.connector)
        for init, target in data['data']['initiator_target_map'].items():
            self.assertIn(init[::-1], target)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST',
                      'FASTPOLICY': 'FC_GOLD1'})
    @mock.patch.object(
        EMCVMAXCommon,
        'find_device_number',
        return_value={'Name': "0001"})
    def test_map_fast_failed(self, mock_wrap_group, mock_maskingview):
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.initialize_connection,
                          self.data.test_volume,
                          self.data.connector)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST',
                      'FASTPOLICY': 'FC_GOLD1'})
    @mock.patch.object(
        EMCVMAXMasking,
        'get_masking_view_by_volume',
        return_value=EMCVMAXCommonData.lunmaskctrl_name)
    def test_detach_fast_success(self, mock_volume_type, mock_maskingview):
        common = self.driver.common
        common.get_target_wwns = mock.Mock(
            return_value=EMCVMAXCommonData.target_wwns)
        data = self.driver.terminate_connection(self.data.test_volume,
                                                self.data.connector)
        common.get_target_wwns.assert_called_once_with(
            EMCVMAXCommonData.storage_system, EMCVMAXCommonData.connector)

        self.assertEqual(0, len(data['data']))

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_size',
        return_value='2147483648')
    def test_extend_volume_fast_success(self, _mock_volume_type,
                                        _mock_volume_size):
        newSize = '2'
        self.driver.extend_volume(self.data.test_volume, newSize)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'check_if_volume_is_extendable',
        return_value='False')
    def test_extend_volume_striped_fast_failed(self, _mock_volume_type,
                                               _mock_is_extendable):
        newSize = '2'
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.extend_volume,
                          self.data.test_volume,
                          newSize)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        FakeDB,
        'volume_get',
        return_value=EMCVMAXCommonData.test_source_volume)
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=[EMCVMAXCommonData.test_volume])
    @mock.patch.object(
        EMCVMAXUtils,
        'get_meta_members_capacity_in_bit',
        return_value=[1234567, 7654321])
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_snapshot_different_sizes_meta_fast_success(
            self, mock_volume_type, mock_volume, mock_meta,
            mock_size, mock_pool, mock_policy):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        common = self.driver.common

        volumeDict = {'classname': u'Symm_StorageVolume',
                      'keybindings': EMCVMAXCommonData.keybindings}
        common.provision.create_volume_from_pool = (
            mock.Mock(return_value=(volumeDict, 0L)))
        common.provision.get_volume_dict_from_job = (
            mock.Mock(return_value=volumeDict))
        common.fast.is_volume_in_default_SG = (
            mock.Mock(return_value=True))
        self.driver.create_snapshot(self.data.test_volume)

    def test_create_snapshot_fast_failed(self):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_snapshot,
                          self.data.test_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        FakeDB,
        'volume_get',
        return_value=EMCVMAXCommonData.test_source_volume)
    @mock.patch.object(
        EMCVMAXCommon,
        '_find_storage_sync_sv_sv',
        return_value=(None, None))
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=None)
    def test_create_clone_simple_volume_fast_success(
            self, mock_volume_type,
            mock_volume, mock_sync_sv, mock_meta):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.driver.common.utils.find_storage_configuration_service = (
            mock.Mock(return_value=EMCVMAXCommonData.storage_system))
        self.driver.common._get_or_create_default_storage_group = (
            mock.Mock(return_value=EMCVMAXCommonData.default_storage_group))
        self.driver.common.fast.is_volume_in_default_SG = (
            mock.Mock(return_value=True))
        self.driver.create_cloned_volume(
            self.data.test_volume,
            EMCVMAXCommonData.test_source_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        FakeDB,
        'volume_get',
        return_value=EMCVMAXCommonData.test_source_volume)
    @mock.patch.object(
        EMCVMAXFast,
        'get_pool_associated_to_policy',
        return_value=1)
    @mock.patch.object(
        EMCVMAXUtils,
        'get_volume_meta_head',
        return_value=[EMCVMAXCommonData.test_volume])
    @mock.patch.object(
        EMCVMAXUtils,
        'get_meta_members_capacity_in_bit',
        return_value=[1234567, 7654321])
    @mock.patch.object(
        EMCVMAXCommon,
        '_get_pool_and_storage_system',
        return_value=(None, EMCVMAXCommonData.storage_system))
    def test_create_clone_fast_failed(
            self, mock_volume_type, mock_vol,
            mock_policy, mock_meta, mock_size, mock_pool):
        self.data.test_volume['volume_name'] = "vmax-1234567"
        self.driver.common._modify_and_get_composite_volume_instance = (
            mock.Mock(return_value=(1L, None)))
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_cloned_volume,
                          self.data.test_volume,
                          EMCVMAXCommonData.test_source_volume)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    def test_migrate_volume_fast_success(self, _mock_volume_type):
        self.driver.migrate_volume(self.data.test_ctxt, self.data.test_volume,
                                   self.data.test_host)

    @mock.patch.object(
        volume_types,
        'get_volume_type_extra_specs',
        return_value={'volume_backend_name': 'FCFAST'})
    @mock.patch.object(
        EMCVMAXUtils,
        'parse_pool_instance_id',
        return_value=('silver', 'SYMMETRIX+000195900551'))
    @mock.patch.object(
        EMCVMAXMasking,
        '_wrap_get_storage_group_from_volume',
        return_value=None)
    def test_retype_volume_fast_success(
            self, _mock_volume_type, mock_values, mock_wrap):
        self.driver.retype(
            self.data.test_ctxt, self.data.test_volume, self.data.new_type,
            self.data.diff, self.data.test_host)

    def _cleanup(self):
        bExists = os.path.exists(self.config_file_path)
        if bExists:
            os.remove(self.config_file_path)
        shutil.rmtree(self.tempdir)
