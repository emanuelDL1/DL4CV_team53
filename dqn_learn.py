import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import torch.optim as optim

import gym
import gym.spaces

import math
import os
import numpy as np
import random
from itertools import count
from collections import namedtuple
from copy import deepcopy

from utils import gray2pytorch, breakout_preprocess
#from environment import Environment

from dqn import DQN

USE_CUDA = torch.cuda.is_available()
tType = torch.cuda.FloatTensor if USE_CUDA else torch.FloatTensor

Transition = namedtuple('Transition', ('state','action','next_state','reward'))
TransitionIdx = namedtuple('transitionIdx', ('idx', 'action', 'reward', 'done'))

path_to_dir = os.getcwd()

class ReplayMemory(object):
	def __init__(self, capacity, num_history_frames = 4):
		self.capacity = capacity
		self.memory = []
		self.memoryTransitions = []
		self.num_frames = 0
		self.memory_full = False
		self.num_transitions = 0
		self.num_history = num_history_frames

	def getCurrentIndex(self):
		return (self.num_frames-1)%self.capacity

	def pushTransition(self,*args):
		if len(self.memoryTransitions) < self.capacity-1:
			self.memoryTransitions.append(None)
		self.memoryTransitions[self.num_transitions] = TransitionIdx(*args)
		self.num_transitions = (self.num_transitions+1)% (self.capacity-1)

	def pushFrame(self, frame):
		if len(self.memory)< self.capacity:
			self.memory.append(None)
		else:
			self.memory_full = True
		self.memory[self.num_frames] = frame
		self.num_frames = (self.num_frames +1)% self.capacity

	def sampleTransition(self, batch_size):
		rnd_transitions = random.sample(self.memoryTransitions, batch_size)
		output = []
		for i in range(len(rnd_transitions)):
			state = self.memory[rnd_transitions[i][0]]
			for j in range(self.num_history-1):
				idx = rnd_transitions[i][0]-1-j
				if not self.memory_full:
					idx = max(0, idx)
				state = torch.cat((self.memory[(idx)%self.capacity], state),1)

			action = rnd_transitions[i][1]
			reward = rnd_transitions[i][2]
			output.append(None)
			if rnd_transitions[i][3]:
				output[i] = Transition(state.type(tType)/255.0, action, None, reward)
			else:
				next_state = self.memory[(rnd_transitions[i][0]+1)%self.capacity]
				for j in range(self.num_history-1):
					idx =  rnd_transitions[i][0]-j
					if not self.memory_full:
						idx = max(0, idx)
					next_state = torch.cat((self.memory[(idx)%self.capacity], next_state),1)
				output[i] = Transition(state.type(tType)/255.0, action, next_state.type(tType)/255.0, reward)
		return output

	def __len__(self):
		return len(self.memory)


def dqn_learning(
	num_frames = 4,
	batch_size = 128,
	mem_size = 524288,
	learning_rate = 0.00015,
	alpha = 0.95,
	epsilon = 0.01,
	start_train_after = 25000,
	num_episodes = 100000,
	update_params_each_k = 10000,
	optimize_each_k = 4
):
    #   num_frames: history of frames as input to DQN
    #   batch_size: size of random samples of memory
	env = gym.make("Breakout-v0")

	num_actions = env.action_space.n

	#envTest = Environment("Breakout-v0", (32, 195, 8, 152), 2, record=False, seed=0)

    #   so far use rgb channels
	model = DQN(channels_in = num_frames,num_actions = num_actions)
	target_model = DQN(channels_in = num_frames, num_actions = num_actions)

	if USE_CUDA:
		model.cuda()
		target_model.cuda()

    #initialize optimizer
	opt = optim.RMSprop(model.parameters(), lr = learning_rate,alpha=alpha, eps=epsilon)
	memory = ReplayMemory(mem_size, num_history_frames = num_frames)

	num_param_updates = 0

    #   greedy_epsilon_selection of an action
	def select_action(dqn, observation,eps):
		rnd = random.random()
		if rnd < eps:
			return torch.LongTensor([[random.randrange(num_actions)]])
		else:
			return dqn(Variable(observation, volatile=True)).type(torch.FloatTensor).data.max(1)[1].view(1,1)

    #   function to optimize model according to reinforcement_q_learning.py's optimization function
	def optimization(last_state, num_param_updates):
        #   not enough memory yet
		if len(memory) < start_train_after:
			return
        #   get random samples
		transitions = memory.sampleTransition(batch_size)
		batch = Transition(*zip(*transitions))

        #   mask of which states are not final states(done = True from env.step)
		non_final_mask = torch.ByteTensor(tuple(map(lambda s: s is not None,
                                          batch.next_state)))

		#for k in range(batch_size):
		#	if batch.next_state[k] is None:
		#		non_final_mask[k] = 1
		#	else:
		#		non_final_mask[k] = 0

		if batch.next_state[0] is None:
			non_final_next_states = last_state
		else:
			non_final_next_states = batch.next_state[0]

		non_final_next_states = Variable(torch.cat(
                    [ns for ns in batch.next_state if ns is not None]),volatile=True)
		state_batch = Variable(torch.cat(batch.state))
		action_batch = Variable(torch.cat(batch.action))
		reward_batch = Variable(torch.cat(batch.reward))

		if USE_CUDA:
			state_batch = state_batch.cuda()
			action_batch = action_batch.cuda()
			reward_batch = reward_batch.cuda()
			non_final_mask = non_final_mask.cuda()
			non_final_next_states = non_final_next_states.cuda()

		state_action_values = torch.gather(model(state_batch),1, action_batch)

		next_max_values = target_model(non_final_next_states).detach().max(1)[0]
		next_state_values = Variable(torch.zeros(batch_size).type(tType))
		next_state_values[non_final_mask]= next_max_values

		#next_state_values[non_final_mask] = model(non_final_next_states).max(1)[0]

		next_state_values.volatile = False
		expected_state_action_values = (next_state_values*0.99) + reward_batch

		opt.zero_grad()

		loss = expected_state_action_values - state_action_values
		loss = loss.clamp(-1,1) * -1.0

		state_action_values.backward(loss.data.unsqueeze(1))

		#loss = F.smooth_l1_loss(state_action_values, expected_state_action_values)
		#loss.backward()
		#for param in model.parameters():
		#	param.grad.data.clamp_(-1,1)

		opt.step()

		if num_param_updates % update_params_each_k  == 0:
			target_model.load_state_dict(model.state_dict())
			print('param update!')

	episodes = num_episodes

	num_steps = 0
	avg_score = 0
	best_score = 0
	torch.save(model.state_dict(),path_to_dir+'\modelParams\paramsStart')
	eps_decay = 15000
	for i in range(episodes):
		env.reset()
		screen = env.render(mode='rgb_array')
		#obsTest = envTest.reset()
        # # list of k last frames
		last_k_frames = []
		for j in range(num_frames):
			last_k_frames.append(None)
			last_k_frames[j] = gray2pytorch(breakout_preprocess(screen))#rgb2gr(get_screen_resize(env))
		if i == 0:
			memory.pushFrame(last_k_frames[0].cpu())
		#last_k_frames = np.squeeze(last_k_frames, axis=1)
		state = torch.cat(last_k_frames,1).type(tType)/255.0

		total_reward = 0
		current_lives = 5
		for t in count():
            # epsilon for greedy epsilon selection, with epsilon decay
			eps = 0.01 + (0.95-0.01)*math.exp(-1.*(num_steps-start_train_after)/eps_decay)
			action = select_action(model, state, eps)
			num_steps +=1
			_, reward, done, info = env.step(action[0,0])#envTest.step(action[0,0])
			lives = info['ale.lives']
			if current_lives != lives:
				current_lives = lives
				#reward = -1.0
            #   clamp rewards
			reward = torch.Tensor([max(-1.0,min(reward,1.0))])
			total_reward += reward[0]

            #   save latest frame, discard oldest
			screen = env.render(mode='rgb_array')
			for j in range(num_frames-1):
				last_k_frames[j] = last_k_frames[j+1]
			last_k_frames[num_frames-1] = gray2pytorch(breakout_preprocess(screen))#torch.from_numpy(envTest.get_observation())#rgb2gr(get_screen_resize(env))

			if not done:
				next_state = torch.cat(last_k_frames,1).type(tType)/255.0
			else:
				next_state = None

            #   save to memory

			memory.pushFrame(last_k_frames[num_frames - 1].cpu())
			memory.pushTransition((memory.getCurrentIndex()-1)%memory.capacity, action, reward, done)
			if num_steps % optimize_each_k==0:
				optimization(state,num_param_updates)
				num_param_updates+=1

			if next_state is not None and USE_CUDA:
				state = next_state.cuda()

			if done:
				break;
			#env.render()
		avg_score += total_reward
		print("episode: ",(i+1),"\treward: ",total_reward, "\tnum steps: ", num_steps)
		if total_reward > best_score:
			best_score = total_reward
		if (i-49) % 50 == 0:
			print("For 50 episodes:\taverage score: ", avg_score/50, "\tbest score so far: ", best_score)
			avg_score = 0
		if (i-200) % 500 == 0:
            		torch.save(model.state_dict(),path_to_dir+'\modelParams\paramsAfter'+str(i))
	torch.save(model.state_dict(),path_to_dir+'\modelParams\paramsFinal')
dqn_learning()

