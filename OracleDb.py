# see also https://pypi.org/project/oracledb/
# see also https://www.beekeeperstudio.io/blog/oracle-database-in-python-tutorial

import time
from tango import AttrQuality, AttrWriteType, DispLevel, DevState, Attr, CmdArgType, UserDefaultAttrProp
from tango.server import Device, attribute, command, DeviceMeta
from tango.server import class_property, device_property
from tango.server import run
import os
import json
from json import JSONDecodeError
import sys,time,datetime,traceback,os
import oracledb

class OracleDb(Device, metaclass=DeviceMeta):

    dsn = device_property(dtype=str, default_value="dbhost.example.com/orclpdb")
    username = device_property(dtype=str, default_value="")
    password = device_property(dtype=str, default_value="")
    database = device_property(dtype=str, default_value="")
    init_dynamic_attributes = device_property(dtype=str, default_value="")
    initial_sql = device_property(dtype=str, default_value="")
    dynamicAttributes = {}
    dynamicAttributeValueTypes = {}
    dynamicAttributeSqlLookup = {}
    last_connect = 0
    connection = 0
    cursor = 0
    
    def connect(self,rethrow=False):
        self.info_stream(f"Connecting to database")
        self.last_connect = time.time()
        try:
            self.connection.close()
            self.info_stream(f"Closed already present connection")
        except: pass

        try:
            self.connection = oracledb.connect(
                dsn=self.dsn,
                host=self.host,
                user=self.username,
                password=self.password,
                autocommit=True)
            self.cursor = self.connection.cursor()
            self.info_stream(f"Connected to {self.host} - {self.database}")
            return True

        except Exception as e:
            self.error_stream(f"Error in connect")
            self.error_stream(traceback.format_exc())
            self.last_error = str(e)
            self.connection = self.cursor = None
            if rethrow: raise e
            return False

    def init_device(self):
        self.set_state(DevState.INIT)
        self.get_device_properties(self.get_device_class())
        self.last_connect,self.last_update,self.last_error = 0,0,''
        self.connect()
        if self.initial_sql != "":
            self.debug_stream(f"Executing initial SQL: {self.initial_sql}")
            self.cursor.execute(self.initial_sql)
        if self.init_dynamic_attributes != "":
            try:
                attributes = json.loads(self.init_dynamic_attributes)
                for attributeData in attributes:
                    self.add_dynamic_attribute(attributeData["name"], 
                        attributeData.get("data_type", ""), attributeData.get("min_value", ""), attributeData.get("max_value", ""),
                        attributeData.get("unit", ""), attributeData.get("write_type", ""), attributeData.get("label", ""),
                        attributeData.get("modifier", ""), attributeData.get("min_alarm", ""), attributeData.get("max_alarm", ""),
                        attributeData.get("min_warning", ""), attributeData.get("max_warning", ""))
            except JSONDecodeError as e:
                attributes = self.init_dynamic_attributes.split(",")
                for attribute in attributes:
                    self.info_stream("Init dynamic attribute: " + str(attribute.strip()))
                    self.add_dynamic_attribute(attribute.strip())
        self.set_state(DevState.ON)

    @command(dtype_in=str, dtype_out=str)
    def sql(self, configStr):
        config = json.loads(configStr)
        sql = config.get("sql")
        params = config.get("params", [])
        self.debug_stream(f"Executing SQL: {sql}")
        rowsAffected = self.cursor.execute(sql, params)
        result = self.cursor.fetchall()
        return json.dumps({"rowsAffected": rowsAffected, "result": result})

    def add_dynamic_attribute(self, topic,
            variable_type_name="DevString", min_value="", max_value="",
            unit="", write_type_name="", label="", modifier="",
            min_alarm="", max_alarm="", min_warning="", max_warning=""):
        self.info_stream(f"Adding dynamic attribute : {topic}")
        if topic == "": return
        prop = UserDefaultAttrProp()
        variableType = self.stringValueToVarType(variable_type_name)
        writeType = self.stringValueToWriteType(write_type_name)
        self.dynamicAttributeValueTypes[topic] = variableType
        self.dynamicAttributeSqlLookup[topic] = modifier
        if(min_value != "" and min_value != max_value): prop.set_min_value(min_value)
        if(max_value != "" and min_value != max_value): prop.set_max_value(max_value)
        if(unit != ""): prop.set_unit(unit)
        if(label != ""): prop.set_label(label)
        if(min_alarm != ""): prop.set_min_alarm(min_alarm)
        if(max_alarm != ""): prop.set_max_alarm(max_alarm)
        if(min_warning != ""): prop.set_min_warning(min_warning)
        if(max_warning != ""): prop.set_max_warning(max_warning)
        attr = Attr(topic, variableType, writeType)
        attr.set_default_properties(prop)
        self.add_attribute(attr, r_meth=self.read_dynamic_attr, w_meth=self.write_dynamic_attr)
        self.dynamicAttributes[topic] = ""
        try:
            result = self.sqlRead(topic)
            if result:
                self.info_stream(f"Attribute {topic} initial SQL read successful, value: {result}")
            else:
                self.warning_stream(f"Attribute {topic} returned empty result from SQL read.")
        except Exception as e:
            self.error_stream(f"Error reading attribute {topic} from database: {str(e)}")


    def stringValueToVarType(self, variable_type_name) -> CmdArgType:
        if(variable_type_name == "DevBoolean"):
            return CmdArgType.DevBoolean
        if(variable_type_name == "DevLong"):
            return CmdArgType.DevLong
        if(variable_type_name == "DevDouble"):
            return CmdArgType.DevDouble
        if(variable_type_name == "DevFloat"):
            return CmdArgType.DevFloat
        if(variable_type_name == "DevString"):
            return CmdArgType.DevString
        if(variable_type_name == ""):
            return CmdArgType.DevString
        raise Exception("given variable_type '" + variable_type + "' unsupported, supported are: DevBoolean, DevLong, DevDouble, DevFloat, DevString")

    def stringValueToWriteType(self, write_type_name) -> AttrWriteType:
        if(write_type_name == "READ"):
            return AttrWriteType.READ
        if(write_type_name == "WRITE"):
            return AttrWriteType.WRITE
        if(write_type_name == "READ_WRITE"):
            return AttrWriteType.READ_WRITE
        if(write_type_name == "READ_WITH_WRITE"):
            return AttrWriteType.READ_WITH_WRITE
        if(write_type_name == ""):
            return AttrWriteType.READ_WRITE
        raise Exception("given write_type '" + write_type_name + "' unsupported, supported are: READ, WRITE, READ_WRITE, READ_WITH_WRITE")

    def stringValueToTypeValue(self, name, val):
        if(self.dynamicAttributeValueTypes[name] == CmdArgType.DevBoolean):
            if(str(val).lower() == "false"):
                return False
            if(str(val).lower() == "true"):
                return True
            return bool(int(float(val)))
        if(self.dynamicAttributeValueTypes[name] == CmdArgType.DevLong):
            return int(float(val))
        if(self.dynamicAttributeValueTypes[name] == CmdArgType.DevDouble):
            return float(val)
        if(self.dynamicAttributeValueTypes[name] == CmdArgType.DevFloat):
            return float(val)
        return val

    def read_dynamic_attr(self, attr):
        name = attr.get_name()
        self.dynamicAttributes[name] = self.sqlRead(name)
        value = self.dynamicAttributes[name]
        self.debug_stream("read value " + str(name) + ": " + str(value))
        attr.set_value(self.stringValueToTypeValue(name, value))

    def write_dynamic_attr(self, attr):
        value = str(attr.get_write_value())
        name = attr.get_name()
        self.debug_stream("write value " + str(name) + ": " + str(value))
        self.dynamicAttributes[name] = value
        self.sqlWrite(name, self.dynamicAttributes[name])
    
    def sqlRead(self, name):
        select = "SELECT `:COL:` as field FROM `:TABLE:` WHERE :WHERE: LIMIT 1;"
        lookup = self.dynamicAttributeSqlLookup[name]
        parts = lookup.split(",")
        if len(parts) != 3:
            raise ValueError(f"Invalid SQL parts for {name}. Modifier expected to contain 3 comma separated values (table_name,column_name,where_part), got: {lookup}")

        select = select.replace(":TABLE:", parts[0])
        select = select.replace(":COL:", parts[1])
        select = select.replace(":WHERE:", parts[2])
        self.debug_stream(f"Executing select SQL: {select}")
        self.cursor.execute(select)
        result = self.cursor.fetchone()
        return result['field'] if result else ""
        
    def sqlWrite(self, name, value):
        update = "UPDATE `:TABLE:` SET `:COL:` = %s WHERE :WHERE: LIMIT 1;"
        parts = self.dynamicAttributeSqlLookup[name].split(",")
        update = update.replace(":TABLE:", parts[0])
        update = update.replace(":COL:", parts[1])
        update = update.replace(":WHERE:", parts[2])
        self.debug_stream(f"Executing update SQL: {update}", (value))
        self.cursor.execute(update, (value))

if __name__ == "__main__":
    deviceServerName = os.getenv("DEVICE_SERVER_NAME")
    run({deviceServerName: OracleDb})
