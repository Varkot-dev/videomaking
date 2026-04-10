What are the next steps. Also lets let's quickly make a super, super    
  arching, broad plan for the next couple of weeks (actual couple of      
  weeks, not Claude weeks), because what you say will take two to three   
  weeks. I'll do it in an hour so I can get an actual broad overview to   
  figure out what the next steps are to improve this project.             
                                                              
⏺ Immediate next steps                                                    
                                                                         
  1. Merge feature/rich-techniques (Session 2 — done, all 5 scenes render,
   361 tests pass)
  2. Merge feature/pipeline-reliability (Session 1 — PR #2 with the loop  
  timing fixes just applied, 371 tests pass)                              
  3. Run a full pipeline smoke test on a real topic (manimgen "gradient   
  descent") to see what the combined improvements actually produce        
                                                                          
  ---                                                                     
  2-week roadmap                                                        
                                                                          
  The project has three dimensions that need to mature together:        
                                                                          
  Quality floor (Week 1)                                                  
                                                                          
  The pipeline currently ships freeze-frame tails and visual defects      
  because the feedback loop is weak. The fixes so far are defensive     
  (codeguard catches bad code). What's missing is observability — after a 
  run, you don't get a clear answer to "how well did this work?"        
                                               
  - Wire get_mismatch_log() into CLI so every run ends with a per-cue A/V 
  diff summary
  - Run 3-4 topics end-to-end, collect the defect patterns, and fix the   
  top 2 systematically                                                    
                                               
  Content depth (Week 1–2)                                                
                                                                        
  The Director has 20+ techniques but the planner doesn't use them well — 
  it gravitates toward axes_curve and stagger_reveal for almost
  everything. The new 3D and motion techniques only help if the planner   
  actually picks them.                                                  
                                               
  - Audit planner output on 5 different topics, measure technique         
  diversity
  - Strengthen planner diversity rules if needed                          
                                                                        
  End-to-end quality bar (Week 2)                                         
   
  Right now there's no definition of "good output." You run the pipeline  
  and watch the video and decide subjectively.                          
                                                                          
  - Define a simple scorecard: no fallbacks, no freeze-frame tails > 1s,  
  no black screens, technique variety          
  - Run the scorecard on each topic, track it across branches so          
  regressions are visible                                                 
                                               
  ---                                                                     
  The honest summary: the pipeline's core mechanics are solid. The next 
  two weeks are about closing the gap between "it runs" and "it produces  
  videos you'd actually want to show someone."