import gym
import rospy
import numpy as np
import math
import time
from math import pi
from geometry_msgs.msg import Twist, Point, Pose
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from std_srvs.srv import Empty
from gym import spaces
from gym_turtlebot3.envs.mytf import euler_from_quaternion
from gym_turtlebot3.envs import Respawn

class TurtleBot3Env(gym.Env):

    def __init__(self, 
            observation_size=24, 
            action_size=5, 
            max_angular_vel=1.5,
            const_linear_vel = 0.15,
            min_range = 0.13,
            max_range = 3.5,
            goalbox_distance = 0.35,
            reward_goal=200,
            reward_collision=-200,
            angle_out = 135,
            continuous=False,
            goal_list=None,
            max_env_size=None
        ):
        
        self.goal_x = 0
        self.goal_y = 0
        self.heading = 0
        self.action_size = action_size
        self.initGoal = True
        self.get_goalbox = False
        self.position = Pose()
        self.pub_cmd_vel = rospy.Publisher('cmd_vel', Twist, queue_size=5)
        self.sub_odom = rospy.Subscriber('odom', Odometry, self.getOdometry)
        self.reset_proxy = rospy.ServiceProxy('gazebo/reset_simulation', Empty)
        self.unpause_proxy = rospy.ServiceProxy('gazebo/unpause_physics', Empty)
        self.pause_proxy = rospy.ServiceProxy('gazebo/pause_physics', Empty)
        self.respawn_goal = Respawn()

        self.respawn_goal.setGoalList(goal_list)

        self.const_linear_vel = const_linear_vel
        self.min_range = min_range
        self.max_range = max_range
        self.goalbox_distance = goalbox_distance
        self.reward_goal = reward_goal
        self.reward_collision = reward_collision
        self.angle_out = angle_out
        self.continuous = continuous

        low = np.append(np.full(observation_size, min_range), np.array([-math.pi, 0], dtype=np.float32))
        high = np.append(np.full(observation_size, max_range), np.array([math.pi, max_env_size], dtype=np.float32))

        if self.continuous:
            self.action_space = spaces.Box(low=-max_angular_vel, high=max_angular_vel, shape=(1,), dtype=np.float32)
        else:
            self.action_space = spaces.Discrete(action_size)
            ang_step = max_angular_vel/((action_size - 1)/2)
            self.actions = [((action_size - 1)/2 - action) * ang_step for action in range(action_size)]


        self.observation_space = spaces.Box(low, high, dtype=np.float32)

        self.start_time = time.time()
        self.last_step_time = self.start_time


    def _getGoalDistace(self):
        goal_distance = round(math.hypot(self.goal_x - self.position.x, self.goal_y - self.position.y), 2)

        return goal_distance


    def getOdometry(self, odom):
        self.position = odom.pose.pose.position
        orientation = odom.pose.pose.orientation
        orientation_list = [orientation.x, orientation.y, orientation.z, orientation.w]
        _, _, yaw = euler_from_quaternion(orientation_list)

        goal_angle = math.atan2(self.goal_y - self.position.y, self.goal_x - self.position.x)

        heading = goal_angle - yaw
        if heading > pi:
            heading -= 2 * pi

        elif heading < -pi:
            heading += 2 * pi

        self.heading = heading


    def getState(self, scan):
        scan_range = []
        heading = self.heading
        done = False

        for i in range(len(scan.ranges)):
            if scan.ranges[i] == float('Inf'):
                scan_range.append(self.max_range)
            elif np.isnan(scan.ranges[i]):
                scan_range.append(0)
            else:
                scan_range.append(scan.ranges[i])

        elapsed_time = time.strftime("%H:%M:%S", time.gmtime(time.time() - self.start_time))

        if self.min_range > min(scan_range) > 0:
            print(f'{elapsed_time}: Collision!!')
            done = True

        if abs(heading) > math.radians(self.angle_out):
            print(f'{elapsed_time}: Out of angle', self.respawn_goal.last_index )
            done = True

        current_distance = self._getGoalDistace()
        if current_distance < self.goalbox_distance:
            print(f'{elapsed_time}: Goal!!')

            if self.respawn_goal.last_index is (self.respawn_goal.len_goal_list - 1):
                done = True
            
            self.get_goalbox = True
                
        return scan_range + [heading, current_distance], done


    def navigationReward(self, heading):
        reference = 1-2*abs(heading)/math.pi
        reward = 5*(reference ** 2)

        if reference < 0:
            reward = -reward

        return reward


    def setReward(self, state, done, action):
                
        if self.get_goalbox:
            reward = self.reward_goal
            self.pub_cmd_vel.publish(Twist())
            self.goal_x, self.goal_y = self.respawn_goal.getPosition(True)
            self.goal_distance = self._getGoalDistace()
            self.get_goalbox = False

        elif done:
            reward = self.reward_collision=-200
            self.pub_cmd_vel.publish(Twist())
            if self.respawn_goal.last_index is not 0:
                self.respawn_goal.initIndex()
                self.goal_x, self.goal_y = self.respawn_goal.getPosition()
                self.goal_distance = self._getGoalDistace()
        
        else:
            heading = state[-2]
            reward = self.navigationReward(heading)

        return reward


    def step(self, action):

        if self.continuous:
            ang_vel = action
        else:
            ang_vel = self.actions[action]

        vel_cmd = Twist()
        vel_cmd.linear.x = self.const_linear_vel
        vel_cmd.angular.z = ang_vel
        self.pub_cmd_vel.publish(vel_cmd)

        data = None
        while data is None:
            try:
                data = rospy.wait_for_message('scan', LaserScan, timeout=5)
            except:
                pass

        state, done = self.getState(data)
        reward = self.setReward(state, done, action)

        return np.asarray(state), reward, done, {}


    def reset(self):
        rospy.wait_for_service('gazebo/reset_simulation')
        try:
            self.reset_proxy()
        except rospy.ServiceException:
            print("gazebo/reset_simulation service call failed")

        data = None
        while data is None:
            try:
                data = rospy.wait_for_message('scan', LaserScan, timeout=5)
            except:
                pass

        if self.initGoal:
            self.goal_x, self.goal_y = self.respawn_goal.getPosition()
            self.initGoal = False
            time.sleep(1)

        self.goal_distance = self._getGoalDistace()
        state, _ = self.getState(data)

        return np.asarray(state)


    def render(self, mode=None):
        pass


    def close(self):
        self.reset()