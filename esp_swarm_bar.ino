#include <WiFi.h>
#include <WiFiUdp.h>

#define PHOTO_PIN 34
#define BUILT_IN_LED 2
#define PWM_CONTROL_LED 4
#define MAX_DEVICES 10

#define Bar_one 23
#define Bar_two 22
#define Bar_three 21
#define Bar_four 19
#define Bar_five 18
#define Bar_six 27
#define Bar_seven 17
#define Bar_eight 16
#define Bar_nine 12
#define Bar_ten 13

const char* WIFI_SSID = "";
const char* WIFI_PASSWORD = "";

IPAddress multicastIP(239, 1, 1, 1);
const unsigned int MULTICAST_PORT = 5000;

WiFiUDP udp;

struct Device{
  String ip;
  unsigned long lastSeen;
  bool isMaster;
  int lightValue;
  uint8_t joinOrder;
};

Device devices[MAX_DEVICES];
uint8_t deviceCount = 0;

int led_macros[] = {Bar_one, Bar_two, Bar_three, Bar_four, Bar_five, Bar_six, Bar_seven, Bar_eight, Bar_nine, Bar_ten};

String myIP = "";
int myJoinOrder = -1;
bool isMaster = false;
int lightValue = 0;
bool resetReceived = false;
int previous_value = 0;

const unsigned long BROADCAST_INTERVAL = 100;
unsigned long cycleStartTime = 0;
unsigned long lastBroadcastTime = 0;
unsigned long lastMasterElection = 0;
const unsigned long MASTER_ELECTION_INTERVAL = 1000;
const unsigned long DEVICE_TIMEOUT = 5000;
unsigned long lastDeviceTimeoutCheck = 0;
unsigned long lastDisplayUpdate = 0;
const unsigned long DISPLAY_UPDATE_INTERVAL = 2000;

bool broadcastedThisCycle = false;

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(PHOTO_PIN, INPUT);
  pinMode(BUILT_IN_LED, OUTPUT);
  pinMode(PWM_CONTROL_LED, OUTPUT);

  digitalWrite(BUILT_IN_LED, LOW);
  digitalWrite(PWM_CONTROL_LED, LOW);

  for(int i = 0; i < 10; i++){
    pinMode(led_macros[i], OUTPUT);
    digitalWrite(led_macros[i], LOW);
  }
  
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  connectToWiFi();
  myIP = WiFi.localIP().toString();
  setupMulticast();
  
  delay(1000);
  
  discoverNetwork();
  assignMyJoinOrder();

  Serial.print("IP: ");
  Serial.print(myIP);
  Serial.print(" | Order: ");
  Serial.println(myJoinOrder);
  
  cycleStartTime = millis();
  delay(500);
}

void loop() {
  receivePackets();
  
  if(resetReceived){
    handleReset();
  }
  
  const unsigned long currentMillis = millis();
  
  lightValue = analogRead(PHOTO_PIN);
  
  if(myJoinOrder >= 0){
    int totalActiveDevices = getTotalActiveDevices();
    
    if(totalActiveDevices > 0){
      unsigned long fullCycleTime = totalActiveDevices * BROADCAST_INTERVAL;
      unsigned long timeInCycle = (currentMillis - cycleStartTime) % fullCycleTime;
      unsigned long myTimeSlot = myJoinOrder * BROADCAST_INTERVAL;
      unsigned long myTimeSlotEnd = myTimeSlot + 5;
      
      if(timeInCycle >= myTimeSlot && timeInCycle < myTimeSlotEnd){
        if(!broadcastedThisCycle){
          broadcastMyInfo();
          lastBroadcastTime = currentMillis;
          broadcastedThisCycle = true;
        }
      }else{
        if(timeInCycle >= myTimeSlotEnd){
          broadcastedThisCycle = false;
        }
      }
    }
  }
  
  if(currentMillis - lastMasterElection >= MASTER_ELECTION_INTERVAL){
    electMaster();
    lastMasterElection = currentMillis;
  }
  
  if(currentMillis - lastDeviceTimeoutCheck >= 1000){
    checkDeviceTimeouts(DEVICE_TIMEOUT);
    lastDeviceTimeoutCheck = currentMillis;
  }
  
  if(currentMillis - lastDisplayUpdate >= DISPLAY_UPDATE_INTERVAL){
    displayDevices();
    lastDisplayUpdate = currentMillis;
  }
  
  updateLEDs();
  yield();
}

void connectToWiFi(){
  Serial.print("WiFi connecting...");
  
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long startAttempt = millis();
  while(WiFi.status() != WL_CONNECTED && millis() - startAttempt < 20000){
    delay(500);
    Serial.print(".");
  }

  if(WiFi.status() == WL_CONNECTED){
    Serial.print(" OK | IP: ");
    Serial.println(WiFi.localIP());
  }else{
    Serial.println(" FAILED - Restarting");
    ESP.restart();
  }
}

void setupMulticast(){
  if(udp.beginMulticast(multicastIP, MULTICAST_PORT)){
    Serial.println("Multicast OK");
  }else{
    Serial.println("Multicast FAILED - Restarting");
    ESP.restart();
  }
}

void discoverNetwork(){
  Serial.print("Discovering network...");
  
  unsigned long discoveryStart = millis();
  while(millis() - discoveryStart < 3000){
    receivePackets();
    delay(10);
  }
  
  Serial.print(" Found ");
  Serial.print(deviceCount);
  Serial.println(" devices");
}

void assignMyJoinOrder(){
  int myIndex = findDeviceIndex(myIP);
  
  if(myIndex >= 0){
    myJoinOrder = devices[myIndex].joinOrder;
    return;
  }
  
  uint8_t highestOrder = 0;
  bool foundDevices = false;
  
  for(int i = 0; i < deviceCount; i++){
    foundDevices = true;
    if(devices[i].joinOrder > highestOrder){
      highestOrder = devices[i].joinOrder;
    }
  }
  
  if(foundDevices){
    myJoinOrder = highestOrder + 1;
  }else{
    myJoinOrder = 0;
  }
  
  addOrUpdateMyself();
}

void addOrUpdateMyself(){
  int myIndex = findDeviceIndex(myIP);
  
  if(myIndex >= 0){
    devices[myIndex].ip = myIP;
    devices[myIndex].lastSeen = millis();
    devices[myIndex].isMaster = isMaster;
    devices[myIndex].lightValue = lightValue;
    devices[myIndex].joinOrder = myJoinOrder;
  }else{
    if(deviceCount < MAX_DEVICES){
      devices[deviceCount].ip = myIP;
      devices[deviceCount].lastSeen = millis();
      devices[deviceCount].isMaster = isMaster;
      devices[deviceCount].lightValue = lightValue;
      devices[deviceCount].joinOrder = myJoinOrder;
      deviceCount++;
    }
  }
}

int findDeviceIndex(String ip){
  for(int i = 0; i < deviceCount; i++){
    if(devices[i].ip == ip){
      return i;
    }
  }
  return -1;
}

int getTotalActiveDevices(){
  int count = 0;
  unsigned long currentTime = millis();
  
  for(int i = 0; i < deviceCount; i++){
    if(currentTime - devices[i].lastSeen < 3000){
      count++;
    }
  }
  
  return count;
}

void receivePackets(){
  int packetsProcessed = 0;
  
  while(true){
    int packetSize = udp.parsePacket();
    
    if(packetSize == 0){
      break;
    }
    
    char buffer[255];
    int len = udp.read(buffer, 255);
    
    if(len > 0){
      buffer[len] = '\0';
      String remoteIP = udp.remoteIP().toString();
      
      if(remoteIP != myIP){
        parseMessage(remoteIP, buffer);
        packetsProcessed++;
      }
    }
    
    if(packetsProcessed > 20){
      break;
    }
  }
}

void parseMessage(String ip, char* msg){
  String message = String(msg);
  
  int idx0 = message.indexOf(',');
  int idx1 = message.indexOf(',', idx0 + 1);
  int idx2 = message.indexOf(',', idx1 + 1);

  if(idx0 == -1 || idx1 == -1 || idx2 == -1){
    return;
  }

  bool master = message.substring(0, idx0).toInt() == 1;
  int lightVal = message.substring(idx0 + 1, idx1).toInt();
  uint8_t joinOrder = message.substring(idx1 + 1, idx2).toInt();
  bool reset = message.substring(idx2 + 1).toInt() == 1;

  if(reset){
    Serial.println("!!! RESET RECEIVED !!!");
    resetReceived = true;
    return;
  }

  updateDevice(ip, master, lightVal, joinOrder);
}

void updateDevice(String ip, bool master, int light, uint8_t joinOrder){
  int index = findDeviceIndex(ip);
  
  if(index >= 0){
    devices[index].ip = ip;
    devices[index].lastSeen = millis();
    devices[index].isMaster = master;
    devices[index].lightValue = light;
    devices[index].joinOrder = joinOrder;
  }else{
    if(deviceCount < MAX_DEVICES){
      devices[deviceCount].ip = ip;
      devices[deviceCount].lastSeen = millis();
      devices[deviceCount].isMaster = master;
      devices[deviceCount].lightValue = light;
      devices[deviceCount].joinOrder = joinOrder;
      deviceCount++;
      
      Serial.print("New device: ");
      Serial.print(ip);
      Serial.print(" Order:");
      Serial.println(joinOrder);
    }
  }
}

void broadcastMyInfo(){
  addOrUpdateMyself();
  
  String message = String(isMaster ? 1 : 0) + "," +
                   String(lightValue) + "," +
                   String(myJoinOrder) + "," +
                   String(0);
  
  udp.beginPacket(multicastIP, MULTICAST_PORT);
  udp.print(message);
  udp.endPacket();
}

void electMaster(){
  int highestLight = -1;
  String masterIP = "";
  
  for(int i = 0; i < deviceCount; i++){
    if(millis() - devices[i].lastSeen < 3000){
      if(devices[i].lightValue > highestLight){
        highestLight = devices[i].lightValue;
        masterIP = devices[i].ip;
      }else if(devices[i].lightValue == highestLight && masterIP != ""){
        if(devices[i].ip < masterIP){
          masterIP = devices[i].ip;
        }
      }
    }
  }
  
  for(int i = 0; i < deviceCount; i++){
    bool shouldBeMaster = (devices[i].ip == masterIP);
    devices[i].isMaster = shouldBeMaster;
  }
  
  bool wasMaster = isMaster;
  isMaster = (masterIP == myIP);
  
  int myIndex = findDeviceIndex(myIP);
  if(myIndex >= 0){
    devices[myIndex].isMaster = isMaster;
  }
  
  if(wasMaster && !isMaster){
    Serial.print("Lost master to: ");
    Serial.println(masterIP);
  }else if(!wasMaster && isMaster){
    Serial.print("NOW MASTER | Light: ");
    Serial.println(lightValue);
  }
}

void handleReset(){
  Serial.print("Reset sequence | Order: ");
  Serial.print(myJoinOrder);
  
  unsigned long delayTime = myJoinOrder * 5000;
  unsigned long startTime = millis();
  
  Serial.print(" | Delay: ");
  Serial.print(delayTime);
  Serial.println("ms");
  
  digitalWrite(BUILT_IN_LED, LOW);
  digitalWrite(PWM_CONTROL_LED, LOW);
  for(int i = 0; i < 10; i++){
    digitalWrite(led_macros[i], LOW);
  }
  while(millis() - startTime < delayTime){
    receivePackets();
    delay(10);
  }
  
  Serial.println("RESTARTING NOW");
  delay(100);
  
  ESP.restart();
}

void updateLEDs(){
  int percent_of_bar = (lightValue*10)/4095;
  if(percent_of_bar > 10) percent_of_bar = 10;

  if(percent_of_bar > previous_value){
    for(int i = previous_value; i < percent_of_bar; i++){
      digitalWrite(led_macros[i], HIGH);
    }
  } else if(percent_of_bar < previous_value){
    for(int i = percent_of_bar; i < previous_value; i++){
      digitalWrite(led_macros[i], LOW);
    }
  }

  previous_value = percent_of_bar;
  
  int brightness = map(lightValue, 0, 4095, 0, 255);
  analogWrite(PWM_CONTROL_LED, brightness);
  digitalWrite(BUILT_IN_LED, isMaster ? HIGH : LOW);
}

void displayDevices(){
  Serial.println("\n===== DEVICES =====");
  
  for(int order = 0; order < MAX_DEVICES; order++){
    for(int i = 0; i < deviceCount; i++){
      if(devices[i].joinOrder == order){
        bool isMe = (devices[i].ip == myIP);
        
        if(isMe) Serial.print(">>> ");
        
        Serial.print("O");
        Serial.print(devices[i].joinOrder);
        Serial.print(": ");
        Serial.print(devices[i].ip);
        Serial.print(" | M:");
        Serial.print(devices[i].isMaster ? "Y" : "N");
        Serial.print(" | L:");
        Serial.print(devices[i].lightValue);
        Serial.print(" | Age:");
        Serial.print((millis() - devices[i].lastSeen) / 1000);
        Serial.print("s");
        
        if(isMe) Serial.print(" (ME)");
        
        Serial.println();
      }
    }
  }
  
  Serial.print("Total: ");
  Serial.print(deviceCount);
  Serial.print(" | Cycle: ");
  Serial.print(getTotalActiveDevices() * BROADCAST_INTERVAL);
  Serial.println("ms");
  Serial.println("===================\n");
}

void removeDevice(String ip){
  int index = findDeviceIndex(ip);
  
  if(index >= 0){
    Serial.print("Timeout: ");
    Serial.println(devices[index].ip);
    
    for(int j = index; j < deviceCount - 1; j++){
      devices[j] = devices[j + 1];
    }
    deviceCount--;
  }
}

void checkDeviceTimeouts(unsigned long timeout){
  unsigned long currentTime = millis();
  
  for(int i = 0; i < deviceCount; i++){
    if(devices[i].ip == myIP){
      continue;
    }
    
    if(currentTime - devices[i].lastSeen > timeout){
      removeDevice(devices[i].ip);
      i--;
    }
  }
}
