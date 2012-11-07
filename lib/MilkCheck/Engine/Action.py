# Copyright CEA (2011) 
# Contributor: TATIBOUET Jeremie <tatibouetj@ocre.cea.fr>

"""
This module contains the Action class definition. It also contains the
definition of a basic event handler and the ActionEventHandler.
"""
# Classes
from datetime import datetime
from ClusterShell.Task import task_self
from ClusterShell.Worker.Popen import WorkerPopen
from ClusterShell.Event import EventHandler
from MilkCheck.Engine.BaseEntity import BaseEntity
from MilkCheck.Callback import call_back_self

# Symbols
from MilkCheck.Engine.BaseEntity import DONE, TIMEOUT, ERROR
from MilkCheck.Engine.BaseEntity import WAITING_STATUS
from MilkCheck.Engine.BaseEntity import NO_STATUS, DEP_ERROR, SKIPPED
from MilkCheck.Callback import EV_COMPLETE, EV_STARTED
from MilkCheck.Callback import EV_TRIGGER_DEP, EV_STATUS_CHANGED

class MilkCheckEventHandler(EventHandler):
    '''
    The basic event handler for MilkCheck derives the class provided
    by ClusterShell to handle events generated by the master task. It contains
    an action as attribute. This action is the element processed through the
    events raised. 
    '''
    
    def __init__(self, action):
        EventHandler.__init__(self)
        assert action, "should not be be None"
        # Current action hooked to the handler
        self._action = action

    def ev_start(self, worker):
        '''Command has been started on a nodeset'''
        if not self._action.parent.simulate:
            call_back_self().notify(self._action, EV_STARTED)

    def ev_timer(self, timer):
        '''
        A timer event is raised when an action was delayed. Now the timer is
        done so we can really execute the action. This method is also used
        to handle action with a service which is specified as ghost. That means
        it does nothing
        '''
        if self._action.parent.simulate:
            self._action.parent.update_status(
                self._action.parent.eval_deps_status())
        else:
            self._action.schedule(allow_delay=False)
       
        
class ActionEventHandler(MilkCheckEventHandler):
    '''
    Inherit from our basic handler and specify others event raised to
    process an action.
    '''
    
    def ev_close(self, worker):
        '''
        This event is raised by the master task as soon as an action is
        done. It specifies the how the action will be computed.
        '''
        # Assign time duration to the current action
        self._action.stop_time = datetime.now()

        # Remove the current action from the running task, this will trigger
        # a redefinition of the current fanout
        action_manager_self().remove_task(self._action)

        # Get back the worker from ClusterShell
        self._action.worker = worker

        # Checkout actions issues
        errors = self._action.nb_errors()
        timeouts = self._action.nb_timeout()

        # Classic Action was failed
        if (errors or timeouts) and self._action.retry > 0:
            self._action.retry -= 1
            self._action.schedule()

        # timeout when more timeouts than permited
        elif timeouts > self._action.errors and errors == 0:
            self._action.update_status(TIMEOUT)
        # failed when too many errors
        elif (errors + timeouts) > self._action.errors:
            self._action.update_status(ERROR)
        else:
            self._action.update_status(DONE)
                
class Action(BaseEntity):
    '''
    This class models an action. An action is generally hooked to a service
    and contains the code and parameters to execute commands over one or several
    nodes of a cluster. An action might have dependencies with other actions.
    '''

    LOCAL_VARIABLES = BaseEntity.LOCAL_VARIABLES.copy()
    LOCAL_VARIABLES['ACTION'] = 'name'
    
    def __init__(self, name, target=None, command=None, timeout=-1, delay=0):
        BaseEntity.__init__(self, name=name, target=target)
        
        # Action's timeout in seconds/milliseconds
        self.timeout = timeout
        
        # Action's delay in seconds
        self.delay = delay
        
        # Number of action's retry
        self._retry = 0
        self._retry_backup = -1
        
        # Command lines that we would like to run 
        self.command = command
        
        # Results and retcodes
        self.worker = None
        
        # Allow us to determine time used by an action within the master task
        self.start_time = None
        self.stop_time = None

    def reset(self):
        '''
        Reset values of attributes in order to used the action multiple time.
        '''
        BaseEntity.reset(self)
        self.start_time = None
        self.stop_time = None
        self.worker = None
        self._retry = self._retry_backup

    def run(self):
        '''Prepare the current action and set up the master task'''
        self.prepare()
        task_self().resume()

    def prepare(self):
        '''
        Prepare is a recursive method allowing the current action to prepare
        actions which are in dependency with her first. An action can only
        be prepared whether the dependencies are not currently running and if
        the current action has not already a status.
        '''
        deps_status = self.eval_deps_status()
        # NO_STATUS and not any dep in progress for the current action
        if self.status is NO_STATUS and deps_status is not WAITING_STATUS:
            if self.skipped():
                self.update_status(SKIPPED)
            elif deps_status is DEP_ERROR or not self.parents:
                self.update_status(WAITING_STATUS)
                self.schedule()
            elif deps_status is DONE:
                # No need to do the action so just make it DONE
                self.update_status(DONE)
            else:
                # Look for uncompleted dependencies
                deps = self.search_deps([NO_STATUS])
                # For each existing deps just prepare it
                for dep in deps:
                    dep.target.prepare()
                    
    def update_status(self, status):
        '''
        This method update the current status of an action. Whether the
        a status meaning that the action is done is specified, the current
        action triggers her direct dependencies.
        '''
        assert status in (NO_STATUS, WAITING_STATUS, DONE, SKIPPED,
                               ERROR, TIMEOUT), 'Bad action status'
        self.status = status
        call_back_self().notify(self, EV_STATUS_CHANGED)
        if status not in (NO_STATUS, WAITING_STATUS):
            if not self.parent.simulate:
                call_back_self().notify(self, EV_COMPLETE)
            if self.children:
                for dep in self.children.values():
                    if dep.target.is_ready():
                        if not self.parent.simulate:
                            call_back_self().notify(
                            (self, dep.target), EV_TRIGGER_DEP)
                        dep.target.prepare()
            else:
                self.parent.update_status(self.status)
        
    def nb_timeout(self):
        '''Return the number of timeout runs.'''
        if self.worker:
            if isinstance(self.worker, WorkerPopen):
                if self.worker.did_timeout():
                    return 1
            else:
                return len(list(self.worker.iter_keys_timeout()))
        return 0
        
    def nb_errors(self):
        '''
        Return the amount of error in the worker.
        '''
        error_count = 0
        if self.worker:
            if isinstance(self.worker, WorkerPopen):
                retcode = self.worker.retcode()
                # We don't count timeout (retcode=None)
                if retcode not in (None, 0):
                    error_count = 1
            else:
                for retcode, nds in self.worker.iter_retcodes():
                    if retcode != 0:
                        error_count += len(nds)
        return error_count
                    
    def set_retry(self, retry):
        '''
        Retry is a property which will be modified during the action life
        cycle. Assigning this property means that the current action has a delay
        greater than 0
        '''
        assert self.delay > 0 , 'No way to specify retry without a delay'
        assert retry >= 0, 'No way to specify a negative retry'
        self._retry = retry
        if self._retry_backup == -1:
            self._retry_backup = retry
        
    def get_retry(self):
        '''Access the property retry in read only'''
        return self._retry
    
    retry = property(fget=get_retry, fset=set_retry) 

    @property
    def duration(self):
        '''
        Task duration in seconds (10^-6) is readable as soon as the task is done
        otherwise it returns None.
        '''
        if not self.start_time or not self.stop_time:
            return None
        else:
            delta = self.stop_time - self.start_time
            return  delta.seconds + (delta.microseconds/1000000.0)

    
    def schedule(self, allow_delay=True):
        '''
        Schedule the current action within the master task. The current action
        could be delayed or fired right now depending of it properties.
        '''
        if not self.start_time:
            self.start_time = datetime.now()
            
        if self.delay > 0 and allow_delay:
            # Action will be started as soon as the timer is done
            action_manager_self().perform_delayed_action(self)
        else:
            # Fire this action
            action_manager_self().perform_action(self)

    def fromdict(self, actdict):
        """Populate action attributes from dict."""
        BaseEntity.fromdict(self, actdict)

        # 'delay' should be set before setting 'retry' (if any)
        if 'delay' in actdict:
            self.delay = actdict['delay']

        if 'cmd' in actdict:
            self.command = actdict['cmd']
        if 'retry' in actdict:
            self.retry = actdict['retry']

from MilkCheck.ActionManager import action_manager_self
